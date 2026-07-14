from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

import requests

from .blue_link_browser import resolve_blue_links
from .settings import DEFAULT_MASTER_API_BASE_URL


WORKSPACE_HEADER = "X-Workspace-Id"


class MasterBlueLinkBackfillError(RuntimeError):
    pass


class MasterBlueLinkBackfillClient:
    def __init__(
        self,
        *,
        workspace_id: str,
        api_base_url: str = DEFAULT_MASTER_API_BASE_URL,
        session: requests.Session | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        self.workspace_id = str(workspace_id or "").strip()
        self.api_base_url = str(api_base_url or "").strip().rstrip("/")
        if not self.workspace_id:
            raise ValueError("workspace_id is required")
        if not self.api_base_url:
            raise ValueError("api_base_url is required")
        self.session = session or requests.Session()
        self.request_timeout = float(request_timeout)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                f"{self.api_base_url}{path}",
                headers={WORKSPACE_HEADER: self.workspace_id},
                json=dict(json) if json is not None else None,
                timeout=self.request_timeout,
            )
            payload = response.json()
        except (requests.RequestException, TypeError, ValueError) as exc:
            raise MasterBlueLinkBackfillError(f"Master 蓝链回流请求失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise MasterBlueLinkBackfillError("Master 蓝链回流响应不是 JSON 对象")
        if response.status_code >= 400:
            detail = str(payload.get("detail") or payload.get("message") or response.text).strip()
            raise MasterBlueLinkBackfillError(
                f"Master 蓝链回流请求失败（HTTP {response.status_code}）：{detail}"
            )
        return payload

    def fetch_browser_pending(self, backfill_id: str) -> dict[str, Any]:
        normalized_id = str(backfill_id or "").strip()
        if not normalized_id:
            raise ValueError("backfill_id is required")
        payload = self._request(
            "GET",
            f"/api/blue-link-backfills/{quote(normalized_id, safe='')}/pending",
        )
        rows = payload.get("browser_pending")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise MasterBlueLinkBackfillError("Master browser_pending 合同无效")
        return payload

    def submit_resolutions(
        self, backfill_id: str, resolutions: list[dict[str, str]]
    ) -> dict[str, Any]:
        normalized_id = str(backfill_id or "").strip()
        if not normalized_id:
            raise ValueError("backfill_id is required")
        if not resolutions:
            raise ValueError("resolutions is required")
        return self._request(
            "POST",
            f"/api/blue-link-backfills/{quote(normalized_id, safe='')}/resolutions",
            json={"resolutions": resolutions},
        )


def resolve_blue_link_backfill(
    backfill_id: str,
    *,
    workspace_id: str,
    master_url: str = DEFAULT_MASTER_API_BASE_URL,
    proxy_url: str | None = None,
    timeout: float = 20.0,
    attempts: int = 2,
    retry_delay: float = 1.0,
    master_timeout: float = 30.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    client = MasterBlueLinkBackfillClient(
        workspace_id=workspace_id,
        api_base_url=master_url,
        session=session,
        request_timeout=master_timeout,
    )
    snapshot = client.fetch_browser_pending(backfill_id)
    source_links = list(
        dict.fromkeys(
            str(row.get("source_link") or "").strip()
            for row in snapshot["browser_pending"]
            if str(row.get("source_link") or "").strip()
        )
    )
    browser_result = resolve_blue_links(
        source_links,
        proxy_url=proxy_url,
        timeout=timeout,
        attempts=attempts,
        retry_delay=retry_delay,
    )
    resolutions = browser_result["resolutions"]
    master_result = (
        client.submit_resolutions(backfill_id, resolutions) if resolutions else snapshot
    )
    final_status = str(master_result.get("status") or snapshot.get("status") or "partial")
    return {
        "ok": final_status == "complete" and not browser_result["unresolved"],
        "status": final_status,
        "backfill_id": str(backfill_id or "").strip(),
        "attempted_count": len(source_links),
        "resolved_count": len(resolutions),
        "suspended_count": len(browser_result["unresolved"]),
        "resolutions": resolutions,
        "unresolved": browser_result["unresolved"],
        "master": master_result,
    }
