from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .db import Database
from .legacy_bridge import install_legacy_paths, try_import
from .md_parser import ParsedMarkdown, parse_markdown_file
from .repositories import Repository
from .settings import DEFAULT_IMAGE_ROOT, DEFAULT_VIDEO_ROOT, DEFAULT_VOICE_ROOT
from .utils import file_metadata, now_iso, safe_text, text_hash


AUDIO_SUFFIXES = {".wav"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi"}
UID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,12}\d[\w-]*)(?![A-Za-z0-9])")


class SyncService:
    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)

    def sync_master_scheme(self, project_id: int, *, apply_changes: bool = True) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先创建或选择品类项目。")
        workspace_id = safe_text(project.get("workspace_id"))
        scheme_id = safe_text(project.get("scheme_id"))
        if not workspace_id or not scheme_id:
            raise ValueError("当前项目缺少 Master workspace_id 或 scheme_id。")

        install_legacy_paths()
        master_schemes = try_import("core.master_schemes")
        if master_schemes is None:
            raise ValueError("无法加载旧项目 Master 方案模块。")
        summary = master_schemes.fetch_scheme_summary(workspace_id=workspace_id, scheme_id=scheme_id)
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

    def sync_markdown(self, project_id: int) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先创建或选择品类项目。")
        md_path = safe_text(project.get("md_path"))
        if not md_path or not Path(md_path).exists():
            raise ValueError("当前项目没有绑定可读取的 MD 文档。")
        parsed = parse_markdown_file(md_path)
        return self.sync_markdown_payload(project_id, parsed)

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
                upserted += self._upsert_script_block(
                    conn,
                    project_id=project_id,
                    script_type="intro",
                    owner_uid="",
                    price_range_label="",
                    block_label=block.label or f"引言{index}",
                    body=block.body,
                    source_anchor=f"引言文案/{block.label or index}",
                    ts=ts,
                )
            for uid in allowed_uids:
                product = md_products.get(uid)
                if not product:
                    continue
                for block in product.scripts:
                    upserted += self._upsert_script_block(
                        conn,
                        project_id=project_id,
                        script_type="product",
                        owner_uid=uid,
                        price_range_label="",
                        block_label=block.label or "正文",
                        body=block.body,
                        source_anchor=f"商品文案/{product.title}-{uid}/{block.label}",
                        ts=ts,
                    )
            for price in parsed.price_transitions:
                for block in price.scripts:
                    upserted += self._upsert_script_block(
                        conn,
                        project_id=project_id,
                        script_type="price_transition",
                        owner_uid="",
                        price_range_label=price.label,
                        block_label=block.label or "正文",
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

    def _upsert_script_block(self, conn, *, project_id: int, script_type: str, owner_uid: str, price_range_label: str, block_label: str, body: str, source_anchor: str, ts: str) -> int:
        block_hash = text_hash(body)
        conn.execute(
            """
            INSERT INTO script_blocks (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source_anchor, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(project_id, script_type, owner_uid, price_range_label, block_label)
            DO UPDATE SET body=excluded.body, text_hash=excluded.text_hash, source_anchor=excluded.source_anchor, active=1, updated_at=excluded.updated_at
            """,
            (project_id, script_type, owner_uid, price_range_label, block_label, body.strip(), block_hash, source_anchor, ts, ts),
        )
        return 1

    def sync_assets(self, project_id: int) -> dict[str, int]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先创建或选择品类项目。")
        products = {item["uid"]: item for item in self.repo.products(project_id, include_removed=False)}
        accounts = self.repo.accounts()
        counts = {"image": 0, "video": 0, "voice": 0, "unmatched": 0}
        items: list[dict[str, Any]] = []
        image_root = Path(safe_text(project.get("image_root")) or DEFAULT_IMAGE_ROOT)
        video_root = Path(safe_text(project.get("video_root")) or DEFAULT_VIDEO_ROOT)
        voice_root = Path(safe_text(project.get("voice_root")) or DEFAULT_VOICE_ROOT)
        for asset_type, root, suffixes in [
            ("image", image_root, IMAGE_SUFFIXES),
            ("video", video_root, VIDEO_SUFFIXES),
            ("voice", voice_root, AUDIO_SUFFIXES),
        ]:
            for path in self._scan_files(root, suffixes):
                uid = self._uid_from_path(path, products)
                account = self._account_from_path(path, accounts)
                if uid:
                    self._upsert_asset(project_id, uid=uid, asset_type=asset_type, path=path, account=account)
                    counts[asset_type] += 1
                else:
                    counts["unmatched"] += 1
                    items.append({"item_kind": "file", "status": "unmatched", "message": "文件名未识别出当前方案 UID", "path": str(path)})
        self.db.log_event(
            project_id,
            "asset_sync",
            "partial" if counts["unmatched"] else "success",
            f"素材同步完成：图片 {counts['image']}，视频 {counts['video']}，配音 {counts['voice']}，未识别 {counts['unmatched']}",
            items[:200],
        )
        return counts

    def _scan_files(self, root: Path, suffixes: set[str]) -> list[Path]:
        if not root.exists():
            return []
        return [path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() in suffixes]

    def _uid_from_path(self, path: Path, products: dict[str, dict[str, Any]]) -> str:
        name = path.stem.casefold()
        for uid in products:
            if uid.casefold() in name:
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
