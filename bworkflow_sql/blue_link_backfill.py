from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote

import requests

from .blue_link_browser import (
    CdpProxyClient,
    TaobaoCouponBrowserResolver,
    resolve_blue_link,
)
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
            "GET", f"/api/blue-link-backfills/{quote(normalized_id, safe='')}/pending"
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

    def submit_browser_attempts(
        self, backfill_id: str, attempts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        normalized_id = str(backfill_id or "").strip()
        if not normalized_id:
            raise ValueError("backfill_id is required")
        if not attempts:
            raise ValueError("attempts is required")
        return self._request(
            "POST",
            f"/api/blue-link-backfills/{quote(normalized_id, safe='')}/browser-attempts",
            json={"attempts": attempts},
        )


class BrowserCircuitStateStore:
    """把平台熔断和最近访问时间持久化到现有 app_settings。"""

    def __init__(self, database: Any | None) -> None:
        self.database = database
        self.memory: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _key(platform: str) -> str:
        return f"blue_link_browser_circuit:{platform}"

    def load(self, platform: str) -> dict[str, Any]:
        key = self._key(platform)
        if self.database is None:
            return dict(self.memory.get(key, {}))
        row = self.database.fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
        if not row:
            return {}
        try:
            payload = json.loads(str(row[0] or "{}"))
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, platform: str, state: Mapping[str, Any]) -> None:
        key = self._key(platform)
        payload = dict(state)
        if self.database is None:
            self.memory[key] = payload
            return
        self.database.execute(
            """
            INSERT INTO app_settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )

    def is_open(self, platform: str, now: float) -> bool:
        return float(self.load(platform).get("open_until") or 0) > now

    def wait_seconds(self, platform: str, now: float, min_interval: float) -> float:
        last_attempt = float(self.load(platform).get("last_attempt_at") or 0)
        return max(0.0, last_attempt + max(0.0, min_interval) - now)

    def mark_attempt(self, platform: str, now: float) -> None:
        state = self.load(platform)
        state["last_attempt_at"] = now
        self.save(platform, state)

    def open(self, platform: str, now: float, cooldown_seconds: float, code: str) -> None:
        state = self.load(platform)
        state.update(
            {
                "open_until": now + max(0.0, cooldown_seconds),
                "opened_at": now,
                "last_code": code,
                "last_attempt_at": now,
            }
        )
        self.save(platform, state)


def resolve_blue_link_backfill(
    backfill_id: str,
    *,
    workspace_id: str,
    master_url: str = DEFAULT_MASTER_API_BASE_URL,
    proxy_url: str | None = None,
    timeout: float = 20.0,
    attempts: int = 1,
    master_timeout: float = 30.0,
    max_links: int = 5,
    jd_min_interval: float = 20.0,
    jd_cooldown_seconds: float = 7200.0,
    session: requests.Session | None = None,
    database: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    # attempts 仅保留命令兼容；批处理固定每条一次，失败交给持久状态机调度。
    _ = attempts
    client = MasterBlueLinkBackfillClient(
        workspace_id=workspace_id,
        api_base_url=master_url,
        session=session,
        request_timeout=master_timeout,
    )
    snapshot = client.fetch_browser_pending(backfill_id)
    rows = [row for row in snapshot["browser_pending"] if str(row.get("source_link") or "").strip()]
    resolver = TaobaoCouponBrowserResolver(
        CdpProxyClient(proxy_url), navigation_timeout=timeout
    ) if rows else None
    circuit = BrowserCircuitStateStore(database)
    resolutions: list[dict[str, str]] = []
    failures: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    attempted_count = 0

    for row in rows:
        if attempted_count >= max(0, int(max_links)):
            skipped.append({"source_link": row["source_link"], "code": "run_budget_exhausted"})
            continue
        platform = str(row.get("platform") or "").strip()
        now = now_fn()
        if platform == "jd" and circuit.is_open("jd", now):
            skipped.append({"source_link": row["source_link"], "code": "jd_circuit_open"})
            continue
        if platform == "jd":
            wait = circuit.wait_seconds("jd", now, jd_min_interval)
            if wait > 0:
                sleep_fn(wait)
            now = now_fn()

        outcome = resolve_blue_link(row["source_link"], resolver=resolver)
        attempted_count += 1
        if platform == "jd":
            circuit.mark_attempt("jd", now)
        if outcome["ok"]:
            resolution = outcome["resolution"]
            client.submit_resolutions(backfill_id, [resolution])
            resolutions.append(resolution)
            continue

        failure = dict(outcome["failure"])
        if not failure.get("platform"):
            failure["platform"] = platform
        client.submit_browser_attempts(backfill_id, [failure])
        failures.append(failure)
        if failure.get("code") == "jd_risk_blocked":
            circuit.open("jd", now, jd_cooldown_seconds, "jd_risk_blocked")

    final_snapshot = client.fetch_browser_pending(backfill_id)
    return {
        "ok": str(final_snapshot.get("status") or "partial") == "complete",
        "status": str(final_snapshot.get("status") or "partial"),
        "backfill_id": str(backfill_id or "").strip(),
        "attempted_count": attempted_count,
        "resolved_count": len(resolutions),
        "failed_count": len(failures),
        "skipped_count": len(skipped),
        "resolutions": resolutions,
        "failures": failures,
        "skipped": skipped,
        "master": final_snapshot,
    }
