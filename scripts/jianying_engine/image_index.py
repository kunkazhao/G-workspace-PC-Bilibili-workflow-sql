from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE_ROOT = Path(r"G:\2026项目-b站\素材-商品ppt图片")
DEFAULT_IMAGE_INDEX_PATH = PROJECT_ROOT / "data" / "jianying_engine" / "image_index.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
UID_RE = re.compile(r"[A-Za-z]{1,10}\d[\w-]*")
UID_TOKEN_RE = re.compile(r"^[A-Za-z]{1,10}\d[\w]*$")


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_name(value: Any) -> str:
    return normalize_text(value).casefold()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_index_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": []}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"图片索引格式异常，不是对象：{path}")
    entries = payload.get("entries")
    scopes = payload.get("scopes")
    if not isinstance(entries, list) and not isinstance(scopes, list):
        raise RuntimeError(f"图片索引格式异常，缺少 entries 或 scopes 列表：{path}")
    if not isinstance(entries, list):
        payload["entries"] = []
    payload.setdefault("version", 1)
    return payload


def iter_index_entries(payload: dict[str, Any]):
    for entry in payload.get("entries", []):
        if isinstance(entry, dict):
            yield entry

    for scope in payload.get("scopes", []):
        if not isinstance(scope, dict):
            continue
        items = scope.get("items")
        if not isinstance(items, dict):
            continue
        for uid, item in items.items():
            if not isinstance(item, dict):
                continue
            yield {
                "category": scope.get("category"),
                "image_user": scope.get("image_user"),
                "image_set": scope.get("image_set"),
                "product_uid": uid,
                "image_path": item.get("image_path"),
            }


def save_index_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_template_dir(
    *,
    category: str,
    image_user: str,
    image_set: str,
    image_root: Path = DEFAULT_IMAGE_ROOT,
) -> Path:
    template_dir = image_root / category / image_user / image_set
    if not template_dir.exists():
        raise FileNotFoundError(f"图片模板目录不存在：{template_dir}")
    if not template_dir.is_dir():
        raise NotADirectoryError(f"图片模板路径不是目录：{template_dir}")
    return template_dir.resolve()


def iter_image_files(template_dir: Path) -> list[Path]:
    files = [
        path
        for path in template_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    return sorted(files, key=lambda path: str(path).casefold())


def extract_uid_from_filename(path: Path) -> str:
    tokens: list[str] = []
    for chunk in re.split(r"[\s_]+", path.stem):
        if not chunk:
            continue
        tokens.extend(part for part in chunk.split("-") if part)
    token_matches = [token for token in tokens if UID_TOKEN_RE.match(token)]
    if token_matches:
        return token_matches[-1].strip()

    matches = UID_RE.findall(path.stem)
    if not matches:
        return ""
    return matches[-1].strip()


def replace_scope_entries(
    entries: list[dict[str, Any]],
    *,
    category: str,
    image_user: str,
    image_set: str,
) -> list[dict[str, Any]]:
    category_key = normalize_name(category)
    user_key = normalize_name(image_user)
    set_key = normalize_name(image_set)
    kept: list[dict[str, Any]] = []
    for entry in entries:
        if (
            normalize_name(entry.get("category")) == category_key
            and normalize_name(entry.get("image_user")) == user_key
            and normalize_name(entry.get("image_set")) == set_key
        ):
            continue
        kept.append(entry)
    return kept


def build_index_entries(
    *,
    category: str,
    image_user: str,
    image_set: str,
    template_dir: Path,
    indexed_at: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    indexed_at = indexed_at or utc_now_iso()
    files = iter_image_files(template_dir)
    if not files:
        raise RuntimeError(f"模板目录里没有可用图片：{template_dir}")

    entries: list[dict[str, Any]] = []
    skipped_files: list[str] = []
    seen_uids: dict[str, Path] = {}
    duplicate_uids: list[str] = []

    for path in files:
        uid = extract_uid_from_filename(path)
        if not uid:
            skipped_files.append(str(path))
            continue
        if uid in seen_uids:
            duplicate_uids.append(uid)
            continue
        seen_uids[uid] = path
        entries.append(
            {
                "category": category,
                "image_user": image_user,
                "image_set": image_set,
                "product_uid": uid,
                "image_path": str(path.resolve()),
                "template_dir": str(template_dir),
                "file_name": path.name,
                "indexed_at": indexed_at,
            }
        )

    if duplicate_uids:
        names = ", ".join(sorted(set(duplicate_uids)))
        raise RuntimeError(f"同一套模板里存在重复商品 UID，无法唯一匹配：{names}")
    if not entries:
        raise RuntimeError(f"模板目录里没有识别到带商品 UID 的图片：{template_dir}")
    return entries, skipped_files


def merge_index_entries(
    *,
    index_path: Path,
    category: str,
    image_user: str,
    image_set: str,
    new_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = ensure_index_payload(index_path)
    existing_entries = payload.get("entries", [])
    kept_entries = replace_scope_entries(
        existing_entries,
        category=category,
        image_user=image_user,
        image_set=image_set,
    )
    payload["entries"] = kept_entries + new_entries
    payload["updated_at"] = utc_now_iso()
    return payload


def resolve_image_paths(
    *,
    index_path: Path,
    category: str,
    image_user: str,
    image_set: str,
    product_uids: list[str],
) -> tuple[dict[str, str], list[str]]:
    payload = ensure_index_payload(index_path)
    category_key = normalize_name(category)
    user_key = normalize_name(image_user)
    set_key = normalize_name(image_set)

    matches: dict[str, str] = {}
    for entry in iter_index_entries(payload):
        if normalize_name(entry.get("category")) != category_key:
            continue
        if normalize_name(entry.get("image_user")) != user_key:
            continue
        if normalize_name(entry.get("image_set")) != set_key:
            continue
        uid = normalize_text(entry.get("product_uid"))
        image_path = normalize_text(entry.get("image_path"))
        if uid and image_path and uid not in matches:
            matches[uid] = image_path

    missing_uids = [uid for uid in product_uids if uid not in matches]
    return matches, missing_uids
