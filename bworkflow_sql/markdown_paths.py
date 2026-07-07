from __future__ import annotations

from pathlib import Path
from typing import Any

from .settings import DEFAULT_MARKDOWN_ROOT, DEFAULT_SPOKEN_MD_ROOT
from .utils import safe_text


def product_copy_library_path(project: dict[str, Any]) -> Path:
    parent = safe_text(project.get("category_parent_name"))
    child = safe_text(project.get("category_name"))
    if parent and child:
        return DEFAULT_MARKDOWN_ROOT / f"{parent}-{child}.md"
    name = safe_text(project.get("name"))
    return DEFAULT_MARKDOWN_ROOT / f"{name}.md"


def project_asset_markdown_path(project: dict[str, Any]) -> tuple[Path, str]:
    bound_path = Path(safe_text(project.get("md_path")))
    library_path = product_copy_library_path(project)
    if not bound_path:
        return library_path, ""
    if _is_under(bound_path, DEFAULT_SPOKEN_MD_ROOT):
        return library_path, "project_md_path_points_to_spoken_artifact"
    if _is_under(bound_path, DEFAULT_MARKDOWN_ROOT):
        return bound_path, ""
    return bound_path, ""


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False
