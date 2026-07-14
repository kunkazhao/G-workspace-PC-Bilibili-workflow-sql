from __future__ import annotations

from typing import Any

from bworkflow_sql import blue_link_backfill
from bworkflow_sql.blue_link_backfill import (
    MasterBlueLinkBackfillClient,
    resolve_blue_link_backfill,
)


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
            }
        ],
    )

    assert pending["browser_pending"][0]["source_link"] == "https://b23.tv/a"
    assert result["status"] == "complete"
    assert session.calls[0]["url"].endswith("/api/blue-link-backfills/job%2F1/pending")
    assert session.calls[0]["headers"] == {"X-Workspace-Id": "workspace-1"}
    assert session.calls[1]["json"] == {
        "resolutions": [
            {
                "source_link": "https://b23.tv/a",
                "resolved_url": "https://item.jd.com/123456.html",
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
                    "browser_pending": [
                        {"source_link": "https://b23.tv/a"},
                        {"source_link": "https://b23.tv/b"},
                    ],
                }
            ),
            FakeResponse(
                {
                    "backfill_id": "job-1",
                    "status": "partial",
                    "matched_count": 1,
                    "unresolved_count": 1,
                }
            ),
        ]
    )

    def fake_resolve(source_links, **kwargs):
        assert list(source_links) == ["https://b23.tv/a", "https://b23.tv/b"]
        assert kwargs["attempts"] == 2
        return {
            "status": "partial",
            "resolutions": [
                {
                    "source_link": "https://b23.tv/a",
                    "resolved_url": "https://item.jd.com/123456.html",
                }
            ],
            "unresolved": [
                {
                    "source_link": "https://b23.tv/b",
                    "status": "suspended",
                    "code": "standard_product_not_reached",
                }
            ],
        }

    monkeypatch.setattr(blue_link_backfill, "resolve_blue_links", fake_resolve)

    result = resolve_blue_link_backfill(
        "job-1",
        workspace_id="workspace-1",
        master_url="http://master.test",
        attempts=2,
        session=session,
    )

    assert result["status"] == "partial"
    assert result["attempted_count"] == 2
    assert result["resolved_count"] == 1
    assert result["suspended_count"] == 1
    assert len(session.calls) == 2


def test_unattended_backfill_does_not_post_when_no_browser_rows(monkeypatch) -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "backfill_id": "job-1",
                    "status": "partial",
                    "browser_pending": [],
                    "pending_count": 4,
                }
            )
        ]
    )
    monkeypatch.setattr(
        blue_link_backfill,
        "resolve_blue_links",
        lambda source_links, **_kwargs: {
            "status": "complete",
            "resolutions": [],
            "unresolved": [],
        },
    )

    result = resolve_blue_link_backfill(
        "job-1",
        workspace_id="workspace-1",
        master_url="http://master.test",
        session=session,
    )

    assert result["attempted_count"] == 0
    assert result["master"]["pending_count"] == 4
    assert len(session.calls) == 1
