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


def ensure_markdown_write_target(
    project: dict[str, Any],
    target_path: str | Path,
    *,
    artifact_kind: str,
) -> Path:
    """Keep reusable copy assets and assembled spoken output from overwriting each other."""
    target = Path(target_path)
    if artifact_kind == "asset":
        protected = safe_text(project.get("spoken_md_path"))
        if protected and _same_path(target, Path(protected)):
            raise ValueError(
                "Markdown ownership violation: materialization cannot write the final spoken Markdown; "
                "only spoken-script assembly owns that output."
            )
        return target
    if artifact_kind == "spoken":
        asset_path, _ = project_asset_markdown_path(project)
        if _same_path(target, asset_path):
            raise ValueError(
                "Markdown ownership violation: spoken-script assembly cannot overwrite reusable asset Markdown."
            )
        return target
    raise ValueError(f"unsupported Markdown artifact kind: {artifact_kind}")


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left.absolute()).casefold() == str(right.absolute()).casefold()
