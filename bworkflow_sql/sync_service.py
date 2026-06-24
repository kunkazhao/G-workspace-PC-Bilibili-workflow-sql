from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .asset_paths import legacy_voice_user_dir, path_is_under, voice_user_dir
from .db import Database, _script_id_slug
from .master_data import MasterDataService
from .md_parser import H3_RE, H4_RE, SCRIPT_ID_RE, SECTION_RE, ParsedMarkdown, parse_markdown_file, parse_product_heading
from .repositories import Repository
from .settings import DEFAULT_IMAGE_ROOT, DEFAULT_VIDEO_ROOT, DEFAULT_VOICE_ROOT
from .utils import file_metadata, now_iso, safe_text, text_hash


AUTOSCAN_AUDIO_SUFFIXES = {".wav", ".mp3"}
MANUAL_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
AUDIO_SUFFIXES = AUTOSCAN_AUDIO_SUFFIXES
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi"}
UID_BOUNDARY_PATTERN = r"(?<![A-Za-z0-9])({uid})(?![A-Za-z0-9])"


def _intro_label(index: int) -> str:
    return f"版本{index}"

def _compact_identity(value: Any) -> str:
    text = safe_text(value).casefold()
    return re.sub(r"[\s_\-—/&]+", "", text)


def _project_identity_tokens(project: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    name = safe_text(project.get("name"))
    if name:
        tokens.update(_compact_identity(part) for part in re.split(r"[-_/\\]+", name) if part.strip())
    md_path = safe_text(project.get("md_path"))
    if md_path:
        tokens.update(_compact_identity(part) for part in re.split(r"[-_/\\]+", Path(md_path).stem) if part.strip())
    tokens.discard("")
    return tokens


def _voice_text_label(block: dict[str, Any], length: int = 2) -> str:
    text = re.sub(r"\s+", "", safe_text(block.get("body")))
    text = re.sub(r"[，。！？、,.!?;；:\"“”‘’（）()【】\[\]]", "", text)
    return text[:length]


class SyncService:
    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)

    def sync_master_scheme(self, project_id: int, *, apply_changes: bool = True, force_refresh: bool = True) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先创建或选择品类项目。")
        workspace_id = safe_text(project.get("workspace_id"))
        scheme_id = safe_text(project.get("scheme_id"))
        if not workspace_id or not scheme_id:
            raise ValueError("当前项目缺少 Master workspace_id 或 scheme_id。")

        summary = MasterDataService().fetch_scheme_summary(workspace_id=workspace_id, scheme_id=scheme_id, force_refresh=force_refresh)
        self._validate_scheme_matches_project(project, summary)
        raw_items = summary.get("items") or []
        products = []
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                continue
            source = item.get("item") if isinstance(item.get("item"), dict) else item
            uid = safe_text(source.get("uid") or item.get("uid"))
            if not uid:
                continue
            products.append(
                {
                    "uid": uid,
                    "title": safe_text(source.get("title") or source.get("name") or item.get("title")),
                    "price_label": safe_text(source.get("price_label") or source.get("price") or item.get("price")),
                    "master_item_id": safe_text(source.get("id") or item.get("item_id") or item.get("id")),
                    "sort_order": index,
                }
            )
        if not apply_changes:
            existing = {item["uid"]: item for item in self.repo.products(project_id)}
            incoming = {item["uid"]: item for item in products}
            return {
                "added": [item for uid, item in incoming.items() if uid not in existing],
                "updated": [item for uid, item in incoming.items() if uid in existing and (existing[uid]["title"] != item["title"] or existing[uid]["price_label"] != item["price_label"])],
                "removed": [item for uid, item in existing.items() if uid not in incoming and not int(item["removed_from_master"])],
            }
        result = self.repo.upsert_products_from_master(project_id, products)
        self.db.log_event(
            project_id,
            "master_scheme_sync",
            "success",
            f"Master 方案同步完成：新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}",
            [
                {"item_kind": "product", "uid": item.get("uid"), "title": item.get("title"), "status": "added", "message": "Master 新增"}
                for item in result["added"]
            ]
            + [
                {"item_kind": "product", "uid": item.get("uid"), "title": item.get("title"), "status": "updated", "message": "Master 信息变更"}
                for item in result["updated"]
            ]
            + [
                {"item_kind": "product", "uid": item.get("uid"), "title": item.get("title"), "status": "removed", "message": "已从 Master 方案移除"}
                for item in result["removed"]
            ],
        )
        return result

    def _validate_scheme_matches_project(self, project: dict[str, Any], summary: dict[str, Any]) -> None:
        project_category_id = safe_text(project.get("category_id"))
        scheme_category_id = safe_text(summary.get("category_id"))
        if project_category_id and scheme_category_id and project_category_id != scheme_category_id:
            raise ValueError(
                "当前项目绑定的 Master 方案和品类不一致，已停止同步，避免把商品刷串。\n"
                f"项目品类：{project.get('category_name') or '--'}（{project_category_id}）\n"
                f"方案品类：{summary.get('category_name') or '--'}（{scheme_category_id}）\n"
                "请回到“品类项目”重新选择正确的二级品类和方案后再同步。"
            )
        if project_category_id and scheme_category_id:
            return
        category_name = safe_text(summary.get("category_name") or project.get("category_name"))
        tokens = _project_identity_tokens(project)
        compact_category = _compact_identity(category_name)
        if compact_category and tokens and compact_category not in tokens:
            raise ValueError(
                "当前项目名称/MD 文件名和 Master 方案品类不一致，已停止同步，避免把商品刷串。\n"
                f"项目：{project.get('name') or '--'}\n"
                f"MD：{project.get('md_path') or '--'}\n"
                f"方案：{summary.get('name') or project.get('scheme_name') or '--'} / {category_name}\n"
                "请确认是否选错了 Master 品类或方案。"
            )

    def sync_markdown(self, project_id: int) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先创建或选择品类项目。")
        md_path = safe_text(project.get("md_path"))
        if not md_path or not Path(md_path).exists():
            raise ValueError("当前项目没有绑定可读取的 MD 文档。")
        path = Path(md_path)
        parsed = parse_markdown_file(path)
        result = self.sync_markdown_payload(project_id, parsed)
        self._write_script_ids_to_markdown(path, parsed)
        return result

    def sync_markdown_payload(self, project_id: int, parsed: ParsedMarkdown) -> dict[str, Any]:
        products = {item["uid"]: item for item in self.repo.products(project_id, include_removed=False)}
        md_products = {item.uid: item for item in parsed.products}
        allowed_uids = set(products)
        extra_md = [item for uid, item in md_products.items() if uid not in allowed_uids]
        missing_copy = [item for uid, item in products.items() if uid not in md_products]
        upserted = 0
        ts = now_iso()
        with self.db.connect() as conn:
            conn.execute("UPDATE script_blocks SET active=0, updated_at=? WHERE project_id=?", (ts, project_id))
            for index, block in enumerate(parsed.intro_scripts, start=1):
                script_id = block.script_id or f"intro:V{index:03d}"
                block.script_id = script_id
                intro_label = block.label or _intro_label(index)
                upserted += self._upsert_script_block(
                    conn,
                    project_id=project_id,
                    script_type="intro",
                    owner_uid="",
                    price_range_label="",
                    block_label=intro_label,
                    script_id=script_id,
                    body=block.body,
                    source_anchor=f"引言文案/{intro_label}",
                    ts=ts,
                )
            for uid in allowed_uids:
                product = md_products.get(uid)
                if not product:
                    continue
                for index, block in enumerate(product.scripts, start=1):
                    script_id = block.script_id or f"product:{uid}:V{index:03d}"
                    block.script_id = script_id
                    upserted += self._upsert_script_block(
                        conn,
                        project_id=project_id,
                        script_type="product",
                        owner_uid=uid,
                        price_range_label="",
                        block_label=block.label or "正文",
                        script_id=script_id,
                        body=block.body,
                        source_anchor=f"商品文案/{product.title}-{uid}/{block.label}",
                        ts=ts,
                    )
            for price in parsed.price_transitions:
                price_key = _script_id_slug(price.label) or f"price-{len(price.label)}"
                for index, block in enumerate(price.scripts, start=1):
                    script_id = block.script_id or f"price:{price_key}:V{index:03d}"
                    block.script_id = script_id
                    upserted += self._upsert_script_block(
                        conn,
                        project_id=project_id,
                        script_type="price_transition",
                        owner_uid="",
                        price_range_label=price.label,
                        block_label=block.label or "正文",
                        script_id=script_id,
                        body=block.body,
                        source_anchor=f"价格过渡文案/{price.label}/{block.label}",
                        ts=ts,
                    )
        items = [
            {"item_kind": "product", "uid": item.uid, "title": item.title, "status": "extra_md", "message": "MD 里有，但不在当前 Master 方案内"}
            for item in extra_md
        ] + [
            {"item_kind": "product", "uid": item["uid"], "title": item["title"], "status": "missing_copy", "message": "当前方案商品在 MD 中缺文案"}
            for item in missing_copy
        ]
        self.db.log_event(
            project_id,
            "markdown_sync",
            "partial" if items else "success",
            f"MD 同步完成：入库文案 {upserted} 条，额外商品 {len(extra_md)}，缺文案商品 {len(missing_copy)}",
            items,
        )
        return {"upserted": upserted, "extra_md": extra_md, "missing_copy": missing_copy}

    def _write_script_ids_to_markdown(self, path: Path, parsed: ParsedMarkdown) -> None:
        try:
            original = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            original = path.read_text(encoding="utf-8", errors="ignore")
        lines = original.splitlines()
        intro_iter = iter(parsed.intro_scripts)
        product_scripts = {item.uid: iter(item.scripts) for item in parsed.products}
        price_scripts = {item.label: iter(item.scripts) for item in parsed.price_transitions}
        section = ""
        current_product_uid = ""
        current_price_label = ""
        pending_script_id = False
        changed = False
        output: list[str] = []

        def append_script_id(script_id: str) -> None:
            nonlocal changed
            if not script_id or pending_script_id:
                return
            output.append(f"<!-- script_id: {script_id} -->")
            changed = True

        for raw in lines:
            stripped = raw.strip()
            if SCRIPT_ID_RE.match(stripped):
                pending_script_id = True
                output.append(raw)
                continue
            section_match = SECTION_RE.match(stripped)
            if section_match:
                section = section_match.group(1).strip()
                current_product_uid = ""
                current_price_label = ""
                pending_script_id = False
                output.append(raw)
                continue
            h3 = H3_RE.match(stripped)
            h4 = H4_RE.match(stripped)
            if h3 and section == "引言文案":
                block = next(intro_iter, None)
                if block:
                    append_script_id(safe_text(block.script_id))
                pending_script_id = False
                output.append(raw)
                continue
            if h3 and section == "商品文案":
                parsed_heading = parse_product_heading(h3.group(1).strip())
                current_product_uid = parsed_heading[0] if parsed_heading else ""
                pending_script_id = False
                output.append(raw)
                continue
            if h3 and section == "价格过渡文案":
                current_price_label = h3.group(1).strip()
                pending_script_id = False
                output.append(raw)
                continue
            if h4 and section == "商品文案" and current_product_uid:
                block = next(product_scripts.get(current_product_uid, iter(())), None)
                if block:
                    append_script_id(safe_text(block.script_id))
                pending_script_id = False
                output.append(raw)
                continue
            if h4 and section == "价格过渡文案" and current_price_label:
                block = next(price_scripts.get(current_price_label, iter(())), None)
                if block:
                    append_script_id(safe_text(block.script_id))
                pending_script_id = False
                output.append(raw)
                continue
            if stripped:
                pending_script_id = False
            output.append(raw)
        if changed:
            path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")

    def _upsert_script_block(self, conn, *, project_id: int, script_type: str, owner_uid: str, price_range_label: str, block_label: str, script_id: str, body: str, source_anchor: str, ts: str) -> int:
        block_hash = text_hash(body)
        conn.execute(
            """
            INSERT INTO script_blocks (project_id, script_type, owner_uid, price_range_label, block_label, script_id, body, text_hash, source_anchor, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(project_id, script_type, owner_uid, price_range_label, block_label)
            DO UPDATE SET script_id=excluded.script_id, body=excluded.body, text_hash=excluded.text_hash, source_anchor=excluded.source_anchor, active=1, updated_at=excluded.updated_at
            """,
            (project_id, script_type, owner_uid, price_range_label, block_label, script_id, body.strip(), block_hash, source_anchor, ts, ts),
        )
        return 1

    def sync_assets(self, project_id: int, *, asset_type: str | None = None, root_override: str | Path | None = None) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先创建或选择品类项目。")
        allowed_types = {"image", "video", "voice"}
        requested_type = asset_type
        if requested_type and requested_type not in allowed_types:
            raise ValueError(f"不支持的素材类型：{asset_type}")
        checked_types = [requested_type] if requested_type else ["image", "video", "voice"]
        md_path = safe_text(project.get("md_path"))
        if "voice" in checked_types and md_path and Path(md_path).exists():
            self.sync_markdown(project_id)
            project = self.repo.project(project_id) or project
        all_products = self.repo.products(project_id)
        products = {item["uid"]: item for item in all_products if item["active"] and not int(item["removed_from_master"])}
        script_blocks = self.repo.script_blocks(project_id)
        accounts = self.repo.accounts()
        all_bindings = self.repo.asset_bindings(project_id)
        blocks_by_type: dict[str, list[dict[str, Any]]] = {}
        for _blk in script_blocks:
            blocks_by_type.setdefault(_blk["script_type"], []).append(_blk)
        counts = {"image": 0, "video": 0, "voice": 0, "unmatched": 0}
        matched_items: list[dict[str, Any]] = []
        matched_keys: set[tuple[str, str, int, str, str, str]] = set()
        matched_uids_by_type: dict[str, set[str]] = {"image": set(), "video": set(), "voice": set()}
        matched_voice_targets: set[tuple[str, str]] = set()
        scanned_roots: dict[str, str] = {}
        items: list[dict[str, Any]] = []
        ts = now_iso()
        before_assets = [
            item
            for item in all_bindings
            if item.get("asset_type") in checked_types and safe_text(item.get("status")) == "ready"
        ]
        before_keys = {self._asset_binding_key(item): item for item in before_assets}
        active_uids = set(products)
        all_uids = {safe_text(row["uid"]) for row in all_products}
        stale_uids = sorted(uid for uid in all_uids if uid and uid not in active_uids)
        if stale_uids:
            placeholders = ", ".join("?" for _ in stale_uids)
            with self.db.connect() as conn:
                conn.execute(
                    f"UPDATE asset_bindings SET status='stale', updated_at=? WHERE project_id=? AND uid IN ({placeholders})",
                    (ts, project_id, *stale_uids),
                )
        image_root = Path(safe_text(project.get("image_root")) or DEFAULT_IMAGE_ROOT)
        video_root = Path(safe_text(project.get("video_root")) or DEFAULT_VIDEO_ROOT)
        voice_root = Path(safe_text(project.get("voice_root")) or DEFAULT_VOICE_ROOT)
        roots = {
            "image": image_root,
            "video": video_root,
            "voice": voice_root,
        }
        if requested_type and root_override:
            roots[requested_type] = Path(root_override)
        uid_patterns = self._build_uid_patterns(products)
        for current_type, root, suffixes in [
            ("image", roots["image"], IMAGE_SUFFIXES),
            ("video", roots["video"], VIDEO_SUFFIXES),
            ("voice", roots["voice"], AUDIO_SUFFIXES),
        ]:
            if requested_type and current_type != requested_type:
                continue
            scanned_roots[current_type] = str(root)
            for path in self._scan_files(root, suffixes):
                uid = self._uid_from_path(path, products, uid_patterns)
                account = {} if current_type == "video" else self._account_from_path(path, accounts)
                if current_type == "voice" and not self._voice_path_in_project_scope(path, project, account):
                    continue
                if uid:
                    block = self._product_voice_block_from_path(uid, path, script_blocks, blocks_by_type) if current_type == "voice" else None
                    if block:
                        self._upsert_voice_block_asset(project_id, uid=uid, block=block, path=path, account=account)
                        matched_voice_targets.add(self._voice_target_key(block))
                        block_label = safe_text(block.get("price_range_label")) if block["script_type"] == "price_transition" else safe_text(block.get("block_label"))
                        matched_keys.add((current_type, uid, int(block["id"]), safe_text(account.get("label")), block_label, str(path)))
                    else:
                        self._upsert_asset(project_id, uid=uid, asset_type=current_type, path=path, account=account)
                        matched_keys.add((current_type, uid, 0, safe_text(account.get("label")), "", str(path)))
                    counts[current_type] += 1
                    matched_uids_by_type[current_type].add(uid)
                    product = products.get(uid) or {}
                    matched_items.append(
                        {
                            "asset_type": current_type,
                            "uid": uid,
                            "title": safe_text(product.get("title")),
                            "account_label": safe_text(account.get("label")),
                            "script_block_id": int(block["id"]) if block else 0,
                            "block_label": block_label if block else "",
                            "path": str(path),
                        }
                    )
                elif current_type == "voice":
                    block = self._voice_block_from_path(path, script_blocks, blocks_by_type)
                    if not block:
                        continue
                    special_uid = "INTRO" if block["script_type"] == "intro" else "PRICE_TRANSITION"
                    target_key = self._voice_target_key(block)
                    self._upsert_voice_block_asset(project_id, uid=special_uid, block=block, path=path, account=account)
                    counts[current_type] += 1
                    matched_voice_targets.add(target_key)
                    block_label = safe_text(block.get("price_range_label")) if block["script_type"] == "price_transition" else safe_text(block.get("block_label"))
                    matched_keys.add((current_type, special_uid, int(block["id"]), safe_text(account.get("label")), block_label, str(path)))
                    matched_items.append(
                        {
                            "asset_type": current_type,
                            "uid": special_uid,
                            "title": self._voice_block_title(block),
                            "account_label": safe_text(account.get("label")),
                            "script_block_id": int(block["id"]),
                            "block_label": block_label,
                            "path": str(path),
                        }
                    )
        if "voice" in checked_types:
            matched_voice_targets.update(self._current_ready_voice_targets(project_id, script_blocks, project, accounts, all_bindings=all_bindings))
        for current_type in checked_types:
            if not current_type:
                continue
            if current_type == "voice":
                for block in script_blocks:
                    target_key = self._voice_target_key(block)
                    if target_key in matched_voice_targets:
                        continue
                    counts["unmatched"] += 1
                    items.append(
                        {
                            "item_kind": "script_block",
                            "uid": self._voice_block_uid(block),
                            "title": self._voice_block_title(block),
                            "status": "missing_asset",
                            "message": "当前文案块缺少配音素材",
                            "path": "",
                            "asset_type": current_type,
                        }
                    )
                continue
            for uid, product in products.items():
                if uid in matched_uids_by_type[current_type]:
                    continue
                counts["unmatched"] += 1
                items.append(
                    {
                        "item_kind": "product",
                        "uid": uid,
                        "title": safe_text(product.get("title")),
                        "status": "missing_asset",
                        "message": f"当前方案商品缺少{self._asset_type_label(current_type)}素材",
                        "path": "",
                        "asset_type": current_type,
                    }
                )
        added_items = [item for item in matched_items if self._asset_item_key(item) not in before_keys]
        _path_cache: dict[str, bool] = {}

        def _cached_path_exists(item: dict[str, Any]) -> bool:
            p = safe_text(item.get("path"))
            if not p:
                return False
            if p not in _path_cache:
                _path_cache[p] = Path(p).is_file()
            return _path_cache[p]

        removed_bindings = [
            item
            for key, item in before_keys.items()
            if key not in matched_keys
            and (
                not _cached_path_exists(item)
                or (
                    self._asset_is_in_scanned_scope(item, scanned_roots)
                    and safe_text(item.get("source_kind")) != "manual"
                )
            )
        ]
        if "voice" in checked_types:
            accounts_by_label = {safe_text(account.get("label")): account for account in accounts}
            removed_bindings.extend(
                item
                for item in before_assets
                if safe_text(item.get("asset_type")) == "voice"
                and safe_text(item.get("source_kind")) != "manual"
                and item not in removed_bindings
                and not self._voice_asset_in_project_scope(item, project, accounts_by_label)
            )
        removed_items = [self._asset_item_from_binding(item) for item in removed_bindings]
        if removed_items:
            removed_ids = [int(item.get("id") or 0) for item in removed_bindings if int(item.get("id") or 0)]
            if removed_ids:
                placeholders = ", ".join("?" for _ in removed_ids)
                with self.db.connect() as conn:
                    conn.execute(
                        f"UPDATE asset_bindings SET status='stale', updated_at=? WHERE project_id=? AND id IN ({placeholders})",
                        (ts, project_id, *removed_ids),
                    )
        current_assets = [
            self._asset_item_from_binding(item)
            for item in self.repo.asset_bindings(project_id)
            if item.get("asset_type") in checked_types and safe_text(item.get("status")) == "ready"
            and _cached_path_exists(item)
            and self._asset_is_in_scanned_scope(item, scanned_roots)
        ]
        for item in current_assets:
            product = products.get(safe_text(item.get("uid"))) or {}
            if product:
                item["title"] = safe_text(product.get("title"))
        self.db.log_event(
            project_id,
            f"asset_sync_{requested_type}" if requested_type else "asset_sync",
            "partial" if counts["unmatched"] else "success",
            f"素材同步完成：图片 {counts['image']}，视频 {counts['video']}，配音 {counts['voice']}，缺素材 {counts['unmatched']}",
            items[:200],
        )
        return {
            **counts,
            "matched_items": matched_items,
            "added_items": added_items,
            "removed_items": removed_items,
            "current_items": current_assets,
            "unmatched_items": items,
            "scanned_roots": scanned_roots,
        }

    def _asset_type_label(self, asset_type: str) -> str:
        return {"image": "图片", "video": "视频", "voice": "配音"}.get(asset_type, "素材")

    def manual_bind_voice_asset(self, project_id: int, *, script_block_id: int, account_label: str, path: str | Path) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先创建或选择品类项目。")
        audio_path = Path(path)
        if not audio_path.exists() or not audio_path.is_file():
            raise ValueError(f"配音文件不存在：{audio_path}")
        if audio_path.suffix.casefold() not in MANUAL_AUDIO_SUFFIXES:
            allowed = "、".join(sorted(MANUAL_AUDIO_SUFFIXES))
            raise ValueError(f"暂不支持的配音格式：{audio_path.suffix}。支持：{allowed}")
        block = self.db.fetchone(
            "SELECT * FROM script_blocks WHERE project_id=? AND id=? AND active=1",
            (project_id, script_block_id),
        )
        if not block:
            raise ValueError("没有找到要绑定的文案块，请先同步 MD。")
        account = self.db.fetchone("SELECT * FROM accounts WHERE label=?", (safe_text(account_label),))
        if not account:
            raise ValueError(f"没有找到用户：{account_label}")
        block_dict = dict(block)
        account_dict = dict(account)
        uid = self._voice_block_uid(block_dict)
        block_label = safe_text(block_dict.get("price_range_label")) if block_dict["script_type"] == "price_transition" else safe_text(block_dict.get("block_label"))
        meta = file_metadata(audio_path)
        ts = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE asset_bindings
                SET status='expired', updated_at=?
                WHERE project_id=?
                  AND script_block_id=?
                  AND asset_type='voice'
                  AND account_label=?
                  AND text_hash<>?
                """,
                (ts, project_id, block_dict["id"], safe_text(account_dict.get("label")), safe_text(block_dict.get("text_hash"))),
            )
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
                VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, ?, ?, 'ready', 'manual', ?, ?, 1, ?, ?)
                ON CONFLICT(project_id, uid, script_block_id, asset_type, account_label, block_label, path)
                DO UPDATE SET
                    account_id=excluded.account_id,
                    script_id=excluded.script_id,
                    text_hash=excluded.text_hash,
                    status='ready',
                    source_kind='manual',
                    file_size=excluded.file_size,
                    file_mtime=excluded.file_mtime,
                    confirmed=1,
                    updated_at=excluded.updated_at
                """,
                (
                    project_id,
                    uid,
                    block_dict["id"],
                    safe_text(account_dict.get("label")),
                    safe_text(account_dict.get("account_id")),
                    block_label,
                    safe_text(block_dict.get("script_id")) or f"script-{block_dict['id']}",
                    safe_text(block_dict.get("text_hash")),
                    str(audio_path),
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    ts,
                ),
            )
        result = {
            "asset_type": "voice",
            "uid": uid,
            "title": self._voice_block_title(block_dict),
            "account_label": safe_text(account_dict.get("label")),
            "block_label": block_label,
            "script_block_id": int(block_dict["id"]),
            "path": str(audio_path),
            "status": "ready",
            "source_kind": "manual",
        }
        self.db.log_event(
            project_id,
            "manual_voice_bind",
            "success",
            f"手动绑定配音：{result['account_label']} / {result['title']}",
            [
                {
                    "item_kind": "script_block",
                    "uid": uid,
                    "title": result["title"],
                    "status": "ready",
                    "message": "手动绑定本地配音文件",
                    "path": str(audio_path),
                }
            ],
        )
        return result

    def _asset_binding_key(self, item: dict[str, Any]) -> tuple[str, str, int, str, str, str]:
        return (
            safe_text(item.get("asset_type")),
            safe_text(item.get("uid")),
            int(item.get("script_block_id") or 0),
            safe_text(item.get("account_label")),
            safe_text(item.get("block_label")),
            safe_text(item.get("path")),
        )

    def _asset_item_key(self, item: dict[str, Any]) -> tuple[str, str, int, str, str, str]:
        return (
            safe_text(item.get("asset_type")),
            safe_text(item.get("uid")),
            int(item.get("script_block_id") or 0),
            safe_text(item.get("account_label")),
            safe_text(item.get("block_label")),
            safe_text(item.get("path")),
        )

    def _asset_item_from_binding(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_type": safe_text(item.get("asset_type")),
            "uid": safe_text(item.get("uid")),
            "title": safe_text(item.get("title")),
            "account_label": safe_text(item.get("account_label")),
            "block_label": safe_text(item.get("block_label")),
            "script_block_id": int(item.get("script_block_id") or 0),
            "path": safe_text(item.get("path")),
            "status": safe_text(item.get("status")),
        }

    def _asset_is_in_scanned_scope(self, item: dict[str, Any], scanned_roots: dict[str, str]) -> bool:
        asset_type = safe_text(item.get("asset_type"))
        root_text = safe_text(scanned_roots.get(asset_type))
        path_text = safe_text(item.get("path"))
        if not root_text or not path_text:
            return False
        path = Path(path_text)
        try:
            path.resolve().relative_to(Path(root_text).resolve())
            return True
        except ValueError:
            return False

    def _asset_path_exists(self, item: dict[str, Any]) -> bool:
        path_text = safe_text(item.get("path"))
        return bool(path_text and Path(path_text).is_file())

    def _current_ready_voice_targets(
        self,
        project_id: int,
        blocks: list[dict[str, Any]],
        project: dict[str, Any],
        accounts: list[dict[str, Any]],
        *,
        all_bindings: list[dict[str, Any]] | None = None,
    ) -> set[tuple[str, str]]:
        bindings = all_bindings if all_bindings is not None else self.repo.asset_bindings(project_id)
        blocks_by_id = {int(block.get("id") or 0): block for block in blocks}
        accounts_by_label = {safe_text(account.get("label")): account for account in accounts}
        path_cache: dict[str, bool] = {}
        targets: set[tuple[str, str]] = set()
        for asset in bindings:
            if safe_text(asset.get("asset_type")) != "voice" or safe_text(asset.get("status")) != "ready":
                continue
            path_text = safe_text(asset.get("path"))
            if not path_text:
                continue
            if path_text not in path_cache:
                path_cache[path_text] = Path(path_text).exists()
            if not path_cache[path_text]:
                continue
            if safe_text(asset.get("source_kind")) != "manual" and not self._voice_asset_in_project_scope(asset, project, accounts_by_label):
                continue
            block = blocks_by_id.get(int(asset.get("script_block_id") or 0))
            if not block:
                continue
            if safe_text(asset.get("text_hash")) != safe_text(block.get("text_hash")):
                continue
            targets.add(self._voice_target_key(block))
        return targets

    def _voice_asset_in_project_scope(
        self,
        asset: dict[str, Any],
        project: dict[str, Any],
        accounts_by_label: dict[str, dict[str, Any]],
    ) -> bool:
        path_text = safe_text(asset.get("path"))
        if not path_text:
            return False
        account_label = safe_text(asset.get("account_label"))
        account = accounts_by_label.get(account_label, {"label": account_label})
        return self._voice_path_in_project_scope(Path(path_text), project, account)

    def _voice_block_from_path(
        self, path: Path, blocks: list[dict[str, Any]], blocks_by_type: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any] | None:
        text = _compact_identity(path.stem)
        stem_segments = {_compact_identity(part) for part in re.split(r"[\s_\-—/&]+", path.stem) if part.strip()}
        intro_blocks = (blocks_by_type or {}).get("intro") or [block for block in blocks if block["script_type"] == "intro"]
        price_blocks = (blocks_by_type or {}).get("price_transition") or [block for block in blocks if block["script_type"] == "price_transition"]
        if "引言" in path.stem:
            segment_hits = [
                block for block in intro_blocks
                if (bl := _compact_identity(safe_text(block.get("block_label")))) and bl in stem_segments
            ]
            if segment_hits:
                return max(segment_hits, key=lambda b: len(safe_text(b.get("block_label"))))
            for block in intro_blocks:
                if self._voice_block_matches_path(block, text):
                    return block
            return intro_blocks[0] if len(intro_blocks) == 1 else None
        if "价格" in path.stem:
            matched_ranges = [
                block
                for block in price_blocks
                if safe_text(block.get("price_range_label")) and _compact_identity(block.get("price_range_label")) in text
            ]
            range_segment_hits = [
                block for block in matched_ranges
                if (bl := _compact_identity(safe_text(block.get("block_label")))) and bl in stem_segments
            ]
            if range_segment_hits:
                return max(range_segment_hits, key=lambda b: len(safe_text(b.get("block_label"))))
            for block in matched_ranges:
                if self._voice_block_matches_path(block, text, include_price=False):
                    return block
            return matched_ranges[0] if len(matched_ranges) == 1 else None
        return None

    def _product_voice_block_from_path(
        self, uid: str, path: Path, blocks: list[dict[str, Any]], blocks_by_type: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any] | None:
        text = _compact_identity(path.stem)
        stem_segments = {_compact_identity(p) for p in re.split(r"[\s_\-—/&]+", path.stem) if p.strip()}
        uid_lower = uid.casefold()
        product_blocks = [
            block for block in ((blocks_by_type or {}).get("product") or blocks)
            if block["script_type"] == "product" and safe_text(block.get("owner_uid")).casefold() == uid_lower
        ]
        segment_hits = [
            block for block in product_blocks
            if (bl := _compact_identity(safe_text(block.get("block_label")))) and bl in stem_segments
        ]
        if segment_hits:
            return max(segment_hits, key=lambda b: len(safe_text(b.get("block_label"))))
        # 其次用原有模糊匹配
        for block in product_blocks:
            if self._voice_block_matches_path(block, text):
                return block
        return product_blocks[0] if len(product_blocks) == 1 else None

    def _voice_block_matches_path(self, block: dict[str, Any], compact_stem: str, *, include_price: bool = True) -> bool:
        candidates = [
            safe_text(block.get("script_id")),
            safe_text(block.get("block_label")),
            _voice_text_label(block),
        ]
        if include_price:
            candidates.append(safe_text(block.get("price_range_label")))
        return any(candidate and _compact_identity(candidate) in compact_stem for candidate in candidates)

    def _voice_target_key(self, block: dict[str, Any]) -> tuple[str, str]:
        return (safe_text(block.get("script_type")), safe_text(block.get("script_id")) or str(block.get("id")))

    def _voice_block_title(self, block: dict[str, Any]) -> str:
        if block["script_type"] == "intro":
            return f"引言 {safe_text(block.get('block_label'))}".strip()
        if block["script_type"] == "price_transition":
            return f"价格过渡 {safe_text(block.get('price_range_label'))}".strip()
        return f"{safe_text(block.get('owner_uid'))} {safe_text(block.get('block_label'))}".strip()

    def _voice_block_uid(self, block: dict[str, Any]) -> str:
        if block["script_type"] == "intro":
            return "INTRO"
        if block["script_type"] == "price_transition":
            return "PRICE_TRANSITION"
        return safe_text(block.get("owner_uid"))

    def _voice_path_in_project_scope(self, path: Path, project: dict[str, Any], account: dict[str, Any]) -> bool:
        account_label = safe_text(account.get("label"))
        voice_root = safe_text(project.get("voice_root")) or DEFAULT_VOICE_ROOT
        if not account_label:
            return True
        current_dir = voice_user_dir(voice_root, project, account_label)
        if path_is_under(path, current_dir):
            return True
        legacy_dir = legacy_voice_user_dir(voice_root, project, account_label)
        return path_is_under(path, legacy_dir)

    def _scan_files(self, root: Path, suffixes: set[str]) -> list[Path]:
        if not root.exists():
            return []
        return [path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() in suffixes]

    def _build_uid_patterns(self, products: dict[str, dict[str, Any]]) -> list[tuple[str, re.Pattern[str]]]:
        entries = []
        for uid in sorted(products, key=len, reverse=True):
            uid_text = safe_text(uid).casefold()
            if uid_text:
                entries.append((uid, re.compile(UID_BOUNDARY_PATTERN.format(uid=re.escape(uid_text)))))
        return entries

    def _uid_from_path(self, path: Path, products: dict[str, dict[str, Any]], uid_patterns: list[tuple[str, re.Pattern[str]]] | None = None) -> str:
        name = path.stem.casefold()
        for uid, pattern in (uid_patterns or self._build_uid_patterns(products)):
            if pattern.search(name):
                return uid
        return ""

    def _account_from_path(self, path: Path, accounts: list[dict[str, Any]]) -> dict[str, Any]:
        text = str(path).casefold()
        for account in accounts:
            for key in ("label", "account_id", "media_identity"):
                value = safe_text(account.get(key)).casefold()
                if value and value in text:
                    return account
        return {}

    def _upsert_asset(self, project_id: int, *, uid: str, asset_type: str, path: Path, account: dict[str, Any]) -> None:
        meta = file_metadata(path)
        ts = now_iso()
        account_label = safe_text(account.get("label"))
        account_id = safe_text(account.get("account_id"))
        path_text = str(path)
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE asset_bindings
                SET account_id=?, status=?, file_size=?, file_mtime=?, updated_at=?
                WHERE project_id=?
                  AND uid=?
                  AND script_block_id IS NULL
                  AND asset_type=?
                  AND account_label=?
                  AND block_label=''
                  AND path=?
                """,
                (
                    account_id,
                    "ready" if meta["exists"] else "path_invalid",
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    project_id,
                    uid,
                    asset_type,
                    account_label,
                    path_text,
                ),
            )
            if cursor.rowcount:
                return
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, asset_type, account_label, account_id, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'scan', ?, ?, 0, ?, ?)
                """,
                (
                    project_id,
                    uid,
                    asset_type,
                    account_label,
                    account_id,
                    path_text,
                    "ready" if meta["exists"] else "path_invalid",
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    ts,
                ),
            )

    def _upsert_voice_block_asset(self, project_id: int, *, uid: str, block: dict[str, Any], path: Path, account: dict[str, Any]) -> None:
        meta = file_metadata(path)
        ts = now_iso()
        account_label = safe_text(account.get("label"))
        account_id = safe_text(account.get("account_id"))
        block_label = safe_text(block.get("price_range_label")) if block["script_type"] == "price_transition" else safe_text(block.get("block_label"))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
                VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, ?, ?, 'ready', 'scan', ?, ?, 0, ?, ?)
                ON CONFLICT(project_id, uid, script_block_id, asset_type, account_label, block_label, path)
                DO UPDATE SET
                    account_id=excluded.account_id,
                    script_id=excluded.script_id,
                    status='ready',
                    file_size=excluded.file_size,
                    file_mtime=excluded.file_mtime,
                    updated_at=excluded.updated_at,
                    text_hash=COALESCE(NULLIF(asset_bindings.text_hash, ''), excluded.text_hash)
                """,
                (
                    project_id,
                    uid,
                    block["id"],
                    account_label,
                    account_id,
                    block_label,
                    safe_text(block.get("script_id")) or f"script-{block['id']}",
                    safe_text(block.get("text_hash")),
                    str(path),
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    ts,
                ),
            )
