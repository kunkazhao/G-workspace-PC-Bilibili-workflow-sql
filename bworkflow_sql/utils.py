from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_text(value: Any) -> str:
    return "\n".join(line.rstrip() for line in safe_text(value).splitlines()).strip()


def text_hash(value: Any) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def file_metadata(path_text: str | Path) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    if not path.exists() or not path.is_file():
        return {"exists": False, "file_size": None, "file_mtime": ""}
    stat = path.stat()
    return {
        "exists": True,
        "file_size": int(stat.st_size),
        "file_mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def compact_path(path_text: str, max_len: int = 70) -> str:
    text = safe_text(path_text)
    if len(text) <= max_len:
        return text
    return f"{text[:28]}...{text[-(max_len - 31):]}"
