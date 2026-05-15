from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import safe_text


SHARED_CLOSING_CATEGORY = "公共-结尾"


def project_category_folder(project: dict[str, Any]) -> str:
    name = safe_text(project.get("name"))
    parent = safe_text(project.get("category_parent_name"))
    category = safe_text(project.get("category_name"))
    if name:
        return name
    if parent and category:
        return f"{parent}-{category}"
    return category or parent


def voice_user_dir(voice_root: str | Path, project: dict[str, Any], account_label: str) -> Path:
    label = safe_text(account_label)
    category = project_category_folder(project)
    root = Path(voice_root)
    if category and label:
        return root / category / label
    if category:
        return root / category
    return root / label if label else root


def legacy_voice_user_dir(voice_root: str | Path, project: dict[str, Any], account_label: str) -> Path:
    label = safe_text(account_label)
    category = safe_text(project.get("category_name")) or project_category_folder(project)
    root = Path(voice_root)
    if category and label:
        return root / f"{label}-{category}"
    return root / label if label else root


def shared_closing_user_dir(voice_root: str | Path, account_label: str) -> Path:
    label = safe_text(account_label)
    return Path(voice_root) / SHARED_CLOSING_CATEGORY / label if label else Path(voice_root) / SHARED_CLOSING_CATEGORY


def path_is_under(path: str | Path, parent: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (OSError, ValueError):
        return False
