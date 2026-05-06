from __future__ import annotations

from typing import Any

from .legacy_bridge import install_legacy_paths, try_import
from .utils import safe_text


class MasterDataService:
    def fetch_workspaces(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        install_legacy_paths()
        master_categories = try_import("core.master_categories")
        if master_categories is None:
            raise RuntimeError("无法加载旧项目的 Master 分类模块。")
        return list(master_categories.fetch_workspaces(force_refresh=force_refresh))

    def fetch_category_tree(self, workspace_id: str, *, force_refresh: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        install_legacy_paths()
        master_categories = try_import("core.master_categories")
        if master_categories is None:
            raise RuntimeError("无法加载旧项目的 Master 分类模块。")
        workspace, tree, source = master_categories.fetch_master_category_tree(
            preferred_workspace_id=workspace_id,
            force_refresh=force_refresh,
            return_source=True,
        )
        return dict(workspace), list(tree), safe_text(source)

    def fetch_schemes(self, *, workspace_id: str, category_id: str, force_refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
        install_legacy_paths()
        master_schemes = try_import("core.master_schemes")
        if master_schemes is None:
            raise RuntimeError("无法加载旧项目的 Master 方案模块。")
        schemes, source = master_schemes.fetch_schemes(
            workspace_id=workspace_id,
            category_id=category_id,
            force_refresh=force_refresh,
            return_source=True,
        )
        return list(schemes), safe_text(source)


def display_name(item: dict[str, Any], fallback: str = "") -> str:
    return safe_text(item.get("name") or item.get("title") or item.get("label") or fallback)
