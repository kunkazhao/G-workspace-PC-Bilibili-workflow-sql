from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .settings import DEFAULT_MASTER_API_BASE_URL
from .utils import safe_text

WORKSPACE_HEADER = "X-Workspace-Id"

_session: requests.Session | None = None
_workspaces_cache: list[dict[str, Any]] | None = None
_category_tree_cache: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
_scheme_list_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
_scheme_summary_cache: dict[tuple[str, str], dict[str, Any]] = {}


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        _session.mount("http://", adapter)
        _session.mount("https://", adapter)
    return _session


def _api_url(path: str) -> str:
    return f"{DEFAULT_MASTER_API_BASE_URL.rstrip('/')}{path}"


def _get_json(path: str, *, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
    try:
        resp = _get_session().get(_api_url(path), headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError as exc:
        raise RuntimeError(f"无法连接 Master API ({DEFAULT_MASTER_API_BASE_URL})：{exc}") from exc
    except requests.HTTPError as exc:
        raise RuntimeError(f"Master API 请求失败：{exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Master API 请求异常：{exc}") from exc


class MasterDataService:
    def fetch_workspaces(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        global _workspaces_cache
        if not force_refresh and _workspaces_cache is not None:
            return [dict(w) for w in _workspaces_cache]
        payload = _get_json("/api/workspaces")
        workspaces = payload.get("workspaces")
        if not isinstance(workspaces, list):
            raise RuntimeError("Master API /api/workspaces 返回异常，缺少 workspaces 列表。")
        _workspaces_cache = [w for w in workspaces if isinstance(w, dict)]
        return [dict(w) for w in _workspaces_cache]

    def _resolve_workspace(self, workspace_id: str, *, force_refresh: bool = False) -> dict[str, Any]:
        workspaces = self.fetch_workspaces(force_refresh=force_refresh)
        if not workspaces:
            raise RuntimeError("Master 中没有可用工作空间。")
        workspace_id = str(workspace_id or "").strip()
        if workspace_id:
            for w in workspaces:
                if str(w.get("id") or "").strip() == workspace_id:
                    return w
        for w in workspaces:
            if str(w.get("slug") or "").strip() == "zhaoer":
                return w
        active = [w for w in workspaces if w.get("is_active", True)]
        return active[0] if active else workspaces[0]

    def fetch_category_tree(self, workspace_id: str, *, force_refresh: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        workspace = self._resolve_workspace(workspace_id, force_refresh=force_refresh)
        ws_id = str(workspace.get("id") or "").strip()
        if not ws_id:
            raise RuntimeError("Master 工作空间缺少 id，无法读取分类。")

        if not force_refresh and ws_id in _category_tree_cache:
            cached_ws, cached_tree = _category_tree_cache[ws_id]
            tree = [dict(p, children=[dict(c) for c in p.get("children", [])]) for p in cached_tree]
            return dict(cached_ws), tree, "memory"

        payload = _get_json("/api/sourcing/categories", headers={WORKSPACE_HEADER: ws_id})
        categories = payload.get("categories")
        if not isinstance(categories, list):
            raise RuntimeError("Master API /api/sourcing/categories 返回异常，缺少 categories 列表。")

        tree = _build_category_tree(categories)
        if not tree:
            raise RuntimeError("Master 分类列表为空。")
        _category_tree_cache[ws_id] = (dict(workspace), [dict(p, children=[dict(c) for c in p.get("children", [])]) for p in tree])
        return workspace, tree, "network"

    def fetch_schemes(self, *, workspace_id: str, category_id: str, force_refresh: bool = False) -> tuple[list[dict[str, Any]], str]:
        cache_key = (workspace_id, category_id)
        if not force_refresh and cache_key in _scheme_list_cache:
            return [dict(s) for s in _scheme_list_cache[cache_key]], "memory"

        payload = _get_json("/api/schemes", headers={WORKSPACE_HEADER: workspace_id}, params={"category_id": category_id})
        schemes = payload.get("schemes")
        if not isinstance(schemes, list):
            raise RuntimeError("Master API /api/schemes 返回异常，缺少 schemes 列表。")
        normalized = [s for s in schemes if isinstance(s, dict)]
        _scheme_list_cache[cache_key] = normalized
        return [dict(s) for s in normalized], "network"

    def fetch_scheme_summary(self, *, workspace_id: str, scheme_id: str, force_refresh: bool = False) -> dict[str, Any]:
        cache_key = (workspace_id, scheme_id)
        if not force_refresh and cache_key in _scheme_summary_cache:
            return dict(_scheme_summary_cache[cache_key])

        payload = _get_json(f"/api/schemes/{scheme_id}/summary", headers={WORKSPACE_HEADER: workspace_id})
        summary = payload.get("scheme")
        if not isinstance(summary, dict):
            raise RuntimeError("Master API /api/schemes/summary 返回异常，缺少 scheme 对象。")
        _scheme_summary_cache[cache_key] = summary
        return dict(summary)


def _build_category_tree(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = []
    for raw in categories:
        if not isinstance(raw, dict):
            continue
        cat_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not cat_id or not name:
            continue
        nodes.append({
            "id": cat_id,
            "name": name,
            "parent_id": str(raw.get("parent_id") or "").strip(),
            "sort_order": int(raw.get("sort_order") or 0),
        })

    parents = sorted(
        [n for n in nodes if not n["parent_id"]],
        key=lambda n: (n["sort_order"], n["name"]),
    )
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        if n["parent_id"]:
            children_by_parent.setdefault(n["parent_id"], []).append(n)

    tree: list[dict[str, Any]] = []
    for parent in parents:
        children = sorted(
            children_by_parent.get(parent["id"], []),
            key=lambda n: (n["sort_order"], n["name"]),
        )
        child_rows = [{"id": c["id"], "name": c["name"]} for c in children]
        if not child_rows:
            child_rows = [{"id": parent["id"], "name": parent["name"]}]
        tree.append({"id": parent["id"], "name": parent["name"], "children": child_rows})
    return tree


def display_name(item: dict[str, Any], fallback: str = "") -> str:
    return safe_text(item.get("name") or item.get("title") or item.get("label") or fallback)
