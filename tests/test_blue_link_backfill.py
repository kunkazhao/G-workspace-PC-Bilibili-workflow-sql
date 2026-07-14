from __future__ import annotations

from typing import Any

from bworkflow_sql import blue_link_backfill
from bworkflow_sql.blue_link_backfill import (
    MasterBlueLinkBackfillClient,
    confirm_blue_link_title_candidates,
    get_blue_link_backfill_report,
    resolve_blue_link_backfill,
)
from bworkflow_sql.cli import build_parser


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_backfill_cli_defaults_to_single_attempt_and_persistent_budget() -> None:
    args = build_parser().parse_args([
        "resolve-blue-link-backfill",
        "job-1",
        "--workspace-id",
        "workspace-1",
    ])

    assert args.attempts == 1
    assert args.max_links == 5
    assert args.jd_min_interval == 20.0
    assert args.jd_cooldown_seconds == 7200.0


def test_master_client_fetches_pending_and_submits_only_url_pairs() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "backfill_id": "job/1",
                    "status": "partial",
                    "browser_pending": [{"source_link": "https://b23.tv/a"}],
                }
            ),
            FakeResponse({"backfill_id": "job/1", "status": "complete"}),
        ]
    )
    client = MasterBlueLinkBackfillClient(
        workspace_id="workspace-1",
        api_base_url="http://master.test/",
        session=session,
    )

    pending = client.fetch_browser_pending("job/1")
    result = client.submit_resolutions(
        "job/1",
        [
                {
                    "source_link": "https://b23.tv/a",
                    "resolved_url": "https://item.jd.com/123456.html",
                    "lease_token": "lease-a",
                }
            ],
            expected_scan_revision=3,
    )

    assert pending["browser_pending"][0]["source_link"] == "https://b23.tv/a"
    assert result["status"] == "complete"
    assert session.calls[0]["url"].endswith("/api/blue-link-backfills/job%2F1/pending")
    assert session.calls[0]["headers"] == {"X-Workspace-Id": "workspace-1"}
    assert session.calls[1]["json"] == {
        "expected_scan_revision": 3,
        "resolutions": [
            {
                "source_link": "https://b23.tv/a",
                "resolved_url": "https://item.jd.com/123456.html",
                "lease_token": "lease-a",
            }
        ]
    }


def test_backfill_report_returns_persisted_groups_and_all_unresolved_items() -> None:
    payload = {
        "backfill_id": "job-1",
        "status": "partial",
        "browser_pending": [],
        "unresolved_item_count": 1,
        "unresolved_groups": [
            {
                "group_type": "browser_deferred",
                "code": "jd_login_required",
                "reason": "京东要求重新登录",
                "count": 1,
                "sample_links": [{"source_link": "https://b23.tv/a"}],
            }
        ],
        "unresolved_items": [
            {
                "source_link": "https://b23.tv/a",
                "browser_last_code": "jd_login_required",
            }
        ],
    }
    session = FakeSession([FakeResponse(payload)])

    result = get_blue_link_backfill_report(
        "job-1",
        workspace_id="workspace-1",
        master_url="http://master.test",
        session=session,
    )

    assert result["unresolved_item_count"] == 1
    assert result["unresolved_groups"][0]["sample_links"][0]["source_link"].endswith("/a")
    assert result["unresolved_items"][0]["browser_last_code"] == "jd_login_required"


def test_title_decisions_are_submitted_as_one_user_approved_batch() -> None:
    session = FakeSession([FakeResponse({"status": "complete", "confirmed_count": 1})])

    result = confirm_blue_link_title_candidates(
        "job-1",
        [
            {
                "source_link": "https://b23.tv/a",
                "action": "confirm",
                "product_id": "p1",
            }
        ],
        workspace_id="workspace-1",
        expected_scan_revision=4,
        decision_batch_id="batch-1",
        master_url="http://master.test",
        session=session,
    )

    assert result["confirmed_count"] == 1
    assert session.calls[0]["url"].endswith("/title-decisions")
    assert session.calls[0]["json"] == {
        "expected_scan_revision": 4,
        "decision_batch_id": "batch-1",
        "decisions": [
            {
                "source_link": "https://b23.tv/a",
                "action": "confirm",
                "product_id": "p1",
            }
        ]
    }


def test_unattended_backfill_resolves_batch_and_submits_successes(monkeypatch) -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "backfill_id": "job-1",
                    "status": "partial",
                    "scan_revision": 2,
                    "title_candidates": [],
                    "browser_pending": [],
                }
            ),
            FakeResponse({
                "browser_pending": [
                    {"source_link": "https://b23.tv/a", "platform": "jd", "lease_token": "lease-a"},
                    {"source_link": "https://b23.tv/b", "platform": "tb", "lease_token": "lease-b"},
                ]
            }),
            FakeResponse({"backfill_id": "job-1", "status": "partial"}),
            FakeResponse({"backfill_id": "job-1", "status": "partial"}),
            FakeResponse(
                {
                    "backfill_id": "job-1",
                    "status": "partial",
                    "matched_count": 1,
                    "unresolved_count": 1,
                    "browser_pending": [],
                }
            ),
        ]
    )

    def fake_resolve(source_link, **_kwargs):
        if source_link.endswith("/a"):
            return {"ok": True, "resolution": {
                    "source_link": "https://b23.tv/a",
                    "resolved_url": "https://item.jd.com/123456.html",
            }}
        return {"ok": False, "failure": {
            "source_link": "https://b23.tv/b",
            "code": "standard_product_not_reached",
            "reason": "no id",
            "landing_url": "https://uland.taobao.com/ccoupon/edetail?e=x",
            "platform": "tb",
            "attempts": 1,
        }}

    monkeypatch.setattr(blue_link_backfill, "resolve_blue_link", fake_resolve)
    monkeypatch.setattr(blue_link_backfill, "CdpProxyClient", lambda _url: object())
    monkeypatch.setattr(blue_link_backfill, "TaobaoCouponBrowserResolver", lambda *_a, **_k: object())

    result = resolve_blue_link_backfill(
        "job-1",
        workspace_id="workspace-1",
        master_url="http://master.test",
        session=session,
        jd_min_interval=0,
    )

    assert result["status"] == "partial"
    assert result["attempted_count"] == 2
    assert result["resolved_count"] == 1
    assert result["failed_count"] == 1
    assert len(session.calls) == 5
    assert session.calls[0]["url"].endswith("/title-candidates")
    assert session.calls[1]["url"].endswith("/browser-leases")
    assert session.calls[2]["json"]["resolutions"][0]["lease_token"] == "lease-a"
    assert session.calls[3]["json"]["attempts"][0]["code"] == "standard_product_not_reached"


def test_unattended_backfill_does_not_post_when_no_browser_rows(monkeypatch) -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "backfill_id": "job-1",
                    "status": "partial",
                    "scan_revision": 2,
                    "title_candidates": [],
                    "browser_pending": [],
                    "pending_count": 4,
                }
            ),
            FakeResponse({"browser_pending": []}),
            FakeResponse(
                {
                    "backfill_id": "job-1",
                    "status": "partial",
                    "browser_pending": [],
                    "pending_count": 4,
                }
            ),
        ]
    )

    result = resolve_blue_link_backfill(
        "job-1",
        workspace_id="workspace-1",
        master_url="http://master.test",
        session=session,
    )

    assert result["attempted_count"] == 0
    assert result["master"]["pending_count"] == 4
    assert len(session.calls) == 3


def test_jd_risk_opens_circuit_skips_later_jd_but_continues_taobao(monkeypatch) -> None:
    pending = {
        "backfill_id": "job-1",
        "status": "partial",
        "scan_revision": 2,
        "title_candidates": [],
        "browser_pending": [
            {"source_link": "https://b23.tv/jd-1", "platform": "jd", "lease_token": "lease-jd-1"},
            {"source_link": "https://b23.tv/jd-2", "platform": "jd", "lease_token": "lease-jd-2"},
            {"source_link": "https://b23.tv/tb-1", "platform": "tb", "lease_token": "lease-tb-1"},
        ],
    }
    session = FakeSession([
        FakeResponse({**pending, "browser_pending": []}),
        FakeResponse(pending),
        FakeResponse({"status": "partial"}),
        FakeResponse({"status": "partial"}),
        FakeResponse({"status": "partial"}),
        FakeResponse({**pending, "browser_pending": []}),
    ])
    opened: list[str] = []

    def fake_resolve(source_link, **_kwargs):
        opened.append(source_link)
        if "jd-1" in source_link:
            return {"ok": False, "failure": {
                "source_link": source_link,
                "code": "jd_risk_blocked",
                "reason": "403",
                "landing_url": "https://pc-frequent-pro.pf.jd.com/?reason=403",
                "platform": "jd",
                "attempts": 1,
            }}
        return {"ok": True, "resolution": {
            "source_link": source_link,
            "resolved_url": "https://detail.tmall.com/item.htm?id=123456",
        }}

    monkeypatch.setattr(blue_link_backfill, "resolve_blue_link", fake_resolve)
    monkeypatch.setattr(blue_link_backfill, "CdpProxyClient", lambda _url: object())
    monkeypatch.setattr(blue_link_backfill, "TaobaoCouponBrowserResolver", lambda *_a, **_k: object())

    result = resolve_blue_link_backfill(
        "job-1",
        workspace_id="workspace-1",
        master_url="http://master.test",
        session=session,
        jd_min_interval=0,
        now_fn=lambda: 1000.0,
    )

    assert opened == ["https://b23.tv/jd-1", "https://b23.tv/tb-1"]
    assert result["skipped"][0]["source_link"] == "https://b23.tv/jd-2"
    assert result["skipped"][0]["code"] == "jd_circuit_open"
