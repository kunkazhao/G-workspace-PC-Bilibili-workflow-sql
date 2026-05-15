from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .asset_paths import legacy_voice_user_dir, voice_user_dir
from .db import Database
from .master_data import MasterDataService, display_name
from .repositories import Repository
from .settings import DEFAULT_IMAGE_ROOT, DEFAULT_VIDEO_ROOT, DEFAULT_VOICE_ROOT, INTERNAL_WORKSPACE_ROOT, LEGACY_PROJECT_ROOT
from .sync_service import SyncService
from .utils import file_metadata, now_iso, safe_text


OLD_ACCOUNTS_PATH = LEGACY_PROJECT_ROOT / "data" / "accounts.json"
OLD_VOICE_REGISTRY_PATH = Path(r"G:\Tools\IndexTTS2.0\outputs\voices\voices.json")
OLD_MEDIA_INDEX_PATH = LEGACY_PROJECT_ROOT / "data" / "media_index.json"


class LegacyImportService:
    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)
        self.sync = SyncService(db)
        self.master = MasterDataService()

    def import_accounts(self) -> int:
        if not OLD_ACCOUNTS_PATH.exists():
            return 0
        payload = _read_json(OLD_ACCOUNTS_PATH)
        rows = payload.get("accounts") if isinstance(payload, dict) else []
        count = 0
        ts = now_iso()
        with self.db.connect() as conn:
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                label = safe_text(row.get("display_name") or row.get("account_id"))
                if not label:
                    continue
                conn.execute(
                    """
                    INSERT INTO accounts (label, account_id, voice_id, voice_name, media_identity, closing_audio_path, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(label) DO UPDATE SET
                        account_id=excluded.account_id,
                        voice_id=excluded.voice_id,
                        voice_name=excluded.voice_name,
                        media_identity=excluded.media_identity,
                        closing_audio_path=excluded.closing_audio_path,
                        updated_at=excluded.updated_at
                    """,
                    (
                        label,
                        safe_text(row.get("account_id") or label),
                        safe_text(row.get("voice_id") or label),
                        label,
                        safe_text(row.get("media_identity") or label),
                        safe_text(row.get("closing_audio_path")),
                        ts,
                        ts,
                    ),
                )
                count += 1
        return count

    def import_voice_profiles(self) -> int:
        if not OLD_VOICE_REGISTRY_PATH.exists():
            return 0
        payload = _read_json(OLD_VOICE_REGISTRY_PATH)
        voices = payload.get("voices") if isinstance(payload, dict) else []
        count = 0
        ts = now_iso()
        with self.db.connect() as conn:
            for row in voices if isinstance(voices, list) else []:
                if not isinstance(row, dict):
                    continue
                voice_id = safe_text(row.get("voice_id"))
                if not voice_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO voice_profiles (voice_id, display_name, speaker_audio_path, emotion_audio_path, source_audio_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(voice_id) DO UPDATE SET
                        display_name=excluded.display_name,
                        speaker_audio_path=excluded.speaker_audio_path,
                        emotion_audio_path=excluded.emotion_audio_path,
                        source_audio_path=excluded.source_audio_path,
                        updated_at=excluded.updated_at
                    """,
                    (
                        voice_id,
                        safe_text(row.get("display_name") or voice_id),
                        safe_text(row.get("speaker_audio_path")),
                        safe_text(row.get("emotion_audio_path")),
                        safe_text(row.get("source_audio_path")),
                        safe_text(row.get("created_at")) or ts,
                        ts,
                    ),
                )
                count += 1
        return count

    def import_category_project(self, *, parent_category: str, category: str, md_path: str | Path) -> dict[str, Any]:
        account_count = self.import_accounts()
        voice_count = self.import_voice_profiles()
        workspace = self._zhaoer_workspace()
        parent, child = self._find_category(workspace_id=safe_text(workspace.get("id")), parent_name=parent_category, child_name=category)
        schemes, _source = self.master.fetch_schemes(workspace_id=safe_text(workspace.get("id")), category_id=safe_text(child.get("id")))
        if not schemes:
            raise ValueError(f"Master 中“{category}”没有可用方案。")
        scheme = schemes[0]
        project_id = self._upsert_project(
            workspace=workspace,
            parent=parent,
            child=child,
            scheme=scheme,
            md_path=Path(md_path),
        )
        master_result = self.sync.sync_master_scheme(project_id, apply_changes=True)
        markdown_result = self.sync.sync_markdown(project_id)
        self._prune_out_of_scope_assets(project_id)
        media_index_counts = self._import_media_index_assets(project_id, parent_category=parent_category, category=category)
        image_count = self._import_images(project_id, parent_category=parent_category, category=category)
        video_count = self._import_videos(project_id, parent_category=parent_category, category=category)
        voice_asset_count = self._import_voice_assets(project_id, parent_category=parent_category, category=category)
        return {
            "project_id": project_id,
            "accounts": account_count,
            "voice_profiles": voice_count,
            "master_added": len(master_result["added"]),
            "master_updated": len(master_result["updated"]),
            "master_removed": len(master_result["removed"]),
            "markdown_upserted": markdown_result["upserted"],
            "images": image_count + media_index_counts["image"],
            "videos": video_count + media_index_counts["video"],
            "voices": voice_asset_count,
        }

    def _zhaoer_workspace(self) -> dict[str, Any]:
        for workspace in self.master.fetch_workspaces():
            if safe_text(workspace.get("name")) == "赵二" or safe_text(workspace.get("slug")) == "zhaoer":
                return workspace
        raise ValueError("Master 中没有找到赵二工作空间。")

    def _find_category(self, *, workspace_id: str, parent_name: str, child_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        _workspace, tree, _source = self.master.fetch_category_tree(workspace_id)
        for parent in tree:
            if safe_text(parent.get("name")) != parent_name:
                continue
            for child in parent.get("children") or []:
                if safe_text(child.get("name")) == child_name:
                    return parent, child
        raise ValueError(f"Master 中没有找到品类：{parent_name}-{child_name}")

    def _upsert_project(self, *, workspace: dict[str, Any], parent: dict[str, Any], child: dict[str, Any], scheme: dict[str, Any], md_path: Path) -> int:
        existing = self.db.fetchone(
            "SELECT id FROM projects WHERE workspace_id=? AND category_id=? AND scheme_id=? ORDER BY id DESC LIMIT 1",
            (safe_text(workspace.get("id")), safe_text(child.get("id")), safe_text(scheme.get("id"))),
        )
        return self.db.upsert_project(
            {
                "id": int(existing["id"]) if existing else 0,
                "name": f"{safe_text(parent.get('name'))}-{safe_text(child.get('name'))}",
                "workspace_id": safe_text(workspace.get("id")),
                "workspace_name": display_name(workspace),
                "category_parent_id": safe_text(parent.get("id")),
                "category_parent_name": safe_text(parent.get("name")),
                "category_id": safe_text(child.get("id")),
                "category_name": safe_text(child.get("name")),
                "scheme_id": safe_text(scheme.get("id")),
                "scheme_name": display_name(scheme, safe_text(scheme.get("id"))),
                "md_path": str(md_path),
                "image_root": str(DEFAULT_IMAGE_ROOT),
                "video_root": str(DEFAULT_VIDEO_ROOT),
                "voice_root": str(DEFAULT_VOICE_ROOT),
                "output_root": str(INTERNAL_WORKSPACE_ROOT),
                "status": "active",
            }
        )

    def _import_images(self, project_id: int, *, parent_category: str, category: str) -> int:
        count = 0
        for root in _candidate_roots(DEFAULT_IMAGE_ROOT, parent_category=parent_category, category=category):
            count += self._import_files_by_uid(project_id, root=root, asset_type="image", category=category)
        return count

    def _import_videos(self, project_id: int, *, parent_category: str, category: str) -> int:
        count = 0
        for root in _candidate_roots(DEFAULT_VIDEO_ROOT, parent_category=parent_category, category=category, include_contains=True):
            count += self._import_files_by_uid(project_id, root=root, asset_type="video", category=category)
        return count

    def _import_media_index_assets(self, project_id: int, *, parent_category: str, category: str) -> dict[str, int]:
        counts = {"image": 0, "video": 0}
        if not OLD_MEDIA_INDEX_PATH.exists():
            return counts
        payload = _read_json(OLD_MEDIA_INDEX_PATH)
        scopes = payload.get("scopes") if isinstance(payload, dict) else []
        products = {item["uid"]: item for item in self.repo.products(project_id, include_removed=False)}
        accounts = self.repo.accounts()
        category_names = {category, f"{parent_category}-{category}"}
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, dict) or safe_text(scope.get("category")) not in category_names:
                continue
            account = _account_by_label(scope.get("image_user"), accounts)
            image_set = safe_text(scope.get("image_set"))
            items = scope.get("items") if isinstance(scope.get("items"), dict) else {}
            for uid, item in items.items():
                uid_text = safe_text(uid)
                if uid_text not in products or not isinstance(item, dict):
                    continue
                image_path = safe_text(item.get("image_path"))
                if image_path:
                    self._upsert_asset(
                        project_id,
                        uid=uid_text,
                        asset_type="image",
                        path=image_path,
                        status="ready",
                        account_label=safe_text(account.get("label")),
                        account_id=safe_text(account.get("account_id")),
                        media_identity=safe_text(account.get("media_identity")),
                        image_set=image_set,
                        block_label="",
                        source_kind="legacy_media_index",
                        source_path=str(OLD_MEDIA_INDEX_PATH),
                    )
                    counts["image"] += 1
                for video_path in item.get("display_videos") or []:
                    path_text = safe_text(video_path)
                    if not path_text:
                        continue
                    self._upsert_asset(
                        project_id,
                        uid=uid_text,
                        asset_type="video",
                        path=path_text,
                        status="ready",
                        account_label="",
                        account_id="",
                        media_identity="",
                        image_set="",
                        block_label="",
                        source_kind="legacy_media_index",
                        source_path=str(OLD_MEDIA_INDEX_PATH),
                    )
                    counts["video"] += 1
        return counts

    def _import_voice_assets(self, project_id: int, *, parent_category: str, category: str) -> int:
        root = DEFAULT_VOICE_ROOT
        accounts = {item["account_id"]: item for item in self.repo.accounts()}
        allowed_uids = {item["uid"] for item in self.repo.products(project_id, include_removed=False)}
        project = {"name": f"{parent_category}-{category}", "category_parent_name": parent_category, "category_name": category}
        count = 0
        registry_paths: list[Path] = []
        seen: set[str] = set()
        for account in accounts.values():
            for candidate in (
                voice_user_dir(root, project, safe_text(account.get("label"))),
                legacy_voice_user_dir(root, project, safe_text(account.get("label"))),
            ):
                registry_path = candidate / "audio_segment_registry.json"
                key = str(registry_path).casefold()
                if key not in seen and registry_path.exists():
                    seen.add(key)
                    registry_paths.append(registry_path)
        if not registry_paths:
            registry_paths = list(root.glob(f"*{category}/audio_segment_registry.json"))
        for registry_path in registry_paths:
            payload = _read_json(registry_path)
            entries = payload.get("entries") if isinstance(payload, dict) else []
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                account_id = safe_text(entry.get("account_id"))
                account = accounts.get(account_id, {})
                uid = safe_text(entry.get("uid"))
                if uid not in allowed_uids and uid not in {"INTRO", "PRICE_TRANSITION", "CLOSING"}:
                    continue
                asset_type = "voice"
                if uid == "INTRO":
                    block_label = safe_text(entry.get("source_label") or "引言")
                elif uid == "PRICE_TRANSITION":
                    block_label = safe_text(entry.get("price_range_label") or entry.get("source_label") or "价格过渡")
                else:
                    block_label = safe_text(entry.get("source_label") or "正文")
                self._upsert_asset(
                    project_id,
                    uid=uid,
                    asset_type=asset_type,
                    path=safe_text(entry.get("audio_path")),
                    status="ready",
                    account_label=safe_text(account.get("label") or account_id),
                    account_id=account_id,
                    media_identity=safe_text(account.get("media_identity") or account_id),
                    block_label=block_label,
                    script_id=safe_text(entry.get("script_id")),
                    text_hash=safe_text(entry.get("text_hash")),
                    source_kind="legacy_audio_registry",
                    source_path=str(registry_path),
                )
                count += 1
        return count

    def _prune_out_of_scope_assets(self, project_id: int) -> None:
        allowed_uids = {item["uid"] for item in self.repo.products(project_id, include_removed=False)}
        allowed_uids.update({"INTRO", "PRICE_TRANSITION", "CLOSING"})
        placeholders = ",".join("?" for _ in allowed_uids)
        with self.db.connect() as conn:
            conn.execute(
                f"DELETE FROM asset_bindings WHERE project_id=? AND uid NOT IN ({placeholders})",
                (project_id, *sorted(allowed_uids)),
            )
            conn.execute("DELETE FROM asset_bindings WHERE project_id=? AND asset_type='voice' AND account_label=''", (project_id,))

    def _import_files_by_uid(self, project_id: int, *, root: Path, asset_type: str, category: str) -> int:
        if not root.exists():
            return 0
        products = self.repo.products(project_id, include_removed=False)
        accounts = self.repo.accounts()
        count = 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            product = _product_from_path(path, products)
            if not product:
                continue
            account = _account_from_path(path, accounts)
            image_set = _image_set_from_path(path, category=category) if asset_type == "image" else ""
            self._upsert_asset(
                project_id,
                uid=safe_text(product.get("uid")),
                asset_type=asset_type,
                path=str(path),
                status="ready",
                account_label=safe_text(account.get("label")),
                account_id=safe_text(account.get("account_id")),
                media_identity=safe_text(account.get("media_identity")),
                image_set=image_set,
                block_label="",
                source_kind="legacy_folder_scan",
                source_path=str(root),
            )
            count += 1
        return count

    def _upsert_asset(
        self,
        project_id: int,
        *,
        uid: str,
        asset_type: str,
        path: str,
        status: str,
        account_label: str = "",
        account_id: str = "",
        media_identity: str = "",
        image_set: str = "",
        block_label: str = "",
        script_id: str = "",
        text_hash: str = "",
        source_kind: str,
        source_path: str = "",
    ) -> None:
        meta = file_metadata(path)
        ts = now_iso()
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE asset_bindings
                SET status=?, account_id=?, media_identity=?, image_set=?, script_id=?, text_hash=?, source_kind=?, source_path=?,
                    file_size=?, file_mtime=?, updated_at=?
                WHERE project_id=? AND uid=? AND asset_type=? AND account_label=? AND block_label=? AND path=?
                """,
                (
                    status if meta["exists"] else "path_invalid",
                    account_id,
                    media_identity,
                    image_set,
                    script_id,
                    text_hash,
                    source_kind,
                    source_path,
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    project_id,
                    uid,
                    asset_type,
                    account_label,
                    block_label,
                    path,
                ),
            )
            if cursor.rowcount:
                return
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, asset_type, account_label, account_id, media_identity, image_set, block_label,
                     script_id, text_hash, path, status, source_kind, source_path, file_size, file_mtime, confirmed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    project_id,
                    uid,
                    asset_type,
                    account_label,
                    account_id,
                    media_identity,
                    image_set,
                    block_label,
                    script_id,
                    text_hash,
                    path,
                    status if meta["exists"] else "path_invalid",
                    source_kind,
                    source_path,
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    ts,
                ),
            )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _candidate_roots(root: Path, *, parent_category: str, category: str, include_contains: bool = False) -> list[Path]:
    names = [category, f"{parent_category}-{category}"]
    result: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path).casefold()
        if key in seen:
            return
        seen.add(key)
        if path.exists() and path.is_dir():
            result.append(path)

    for name in names:
        add(root / name)
    if include_contains and root.exists():
        for child in root.iterdir():
            if child.is_dir() and category in child.name:
                add(child)
    return result


def _account_by_label(label: Any, accounts: list[dict[str, Any]]) -> dict[str, Any]:
    label_text = safe_text(label)
    for account in accounts:
        if safe_text(account.get("label")) == label_text:
            return account
    return {}


def _product_from_path(path: Path, products: list[dict[str, Any]]) -> dict[str, Any]:
    by_uid = {safe_text(item.get("uid")): item for item in products}
    uid = _uid_from_path(path, by_uid)
    if uid:
        return by_uid.get(uid, {})
    name = _normalize_match_text(path.stem)
    for product in products:
        title = _normalize_match_text(product.get("title"))
        if title and title in name:
            return product
    order = _leading_order(path.stem)
    if order is not None:
        for product in products:
            if int(product.get("sort_order") or 0) == order:
                return product
    return {}


def _uid_from_path(path: Path, products: dict[str, dict[str, Any]]) -> str:
    name = path.stem.casefold()
    for uid in products:
        if uid.casefold() in name:
            return uid
    return ""


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"[\s_\-./\\()（）【】\[\]：:]+", "", safe_text(value).casefold())


def _leading_order(value: str) -> int | None:
    match = re.match(r"^\s*(\d+)[-_ ]", value)
    return int(match.group(1)) if match else None


def _account_from_path(path: Path, accounts: list[dict[str, Any]]) -> dict[str, Any]:
    text = str(path).casefold()
    for account in accounts:
        for key in ("label", "account_id", "media_identity"):
            value = safe_text(account.get(key)).casefold()
            if value and value in text:
                return account
    return {}


def _image_set_from_path(path: Path, *, category: str) -> str:
    parts = list(path.parts)
    if category in parts:
        index = parts.index(category)
        if len(parts) > index + 2:
            return safe_text(parts[index + 2])
    return ""
