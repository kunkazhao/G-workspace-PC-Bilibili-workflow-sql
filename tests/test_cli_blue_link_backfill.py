from __future__ import annotations

import pytest

from bworkflow_sql import blue_link_backfill, cli, production_history


def _args() -> object:
    return cli.build_parser().parse_args(
        [
            "record-blue-link-backfill",
            "7",
            "--pipeline",
            "pipeline.json",
            "--backfill-id",
            "job-1",
            "--workspace-id",
            "workspace-1",
        ]
    )


def test_record_backfill_uses_master_snapshot_not_manual_counts(monkeypatch) -> None:
    recorded = {}

    class FakeService:
        def __init__(self, _repo) -> None:
            pass

        def publishing_context(self, _production_run_id: int):
            return {
                "master_account_id": "account-1",
                "scheme_id": "scheme-1",
                "bilibili_mid": "mid-1",
            }

        def record_blue_link_backfill(self, _production_run_id: int, **kwargs):
            recorded.update(kwargs)
            return {"ok": True}

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def fetch_browser_pending(self, _backfill_id: str):
            return {
                "account_id": "account-1",
                "scheme_id": "scheme-1",
                "video_owner_mid": "mid-1",
                "production_run_id": "7",
                "video_url": "https://www.bilibili.com/video/BV1test",
                "bvid": "BV1test",
                "aid": 123,
                "status": "partial",
                "matched_count": 8,
                "unresolved_count": 4,
                "browser_pending_count": 1,
                "browser_deferred_count": 1,
                "browser_suspended_count": 1,
                "master_data_pending_count": 1,
                "browser_pending": [],
            }

    monkeypatch.setattr(cli, "_init", lambda: (object(), object(), object(), object()))
    monkeypatch.setattr(cli, "_json_out", lambda _payload: None)
    monkeypatch.setattr(production_history, "ProductionHistoryService", FakeService)
    monkeypatch.setattr(blue_link_backfill, "MasterBlueLinkBackfillClient", FakeClient)

    cli.cmd_record_blue_link_backfill(_args())

    assert recorded["matched_count"] == 8
    assert recorded["unresolved_count"] == 4
    assert recorded["browser_suspended_count"] == 1
    assert recorded["master_pending_count"] == 1


def test_record_backfill_rejects_legacy_count_that_disagrees_with_master(monkeypatch) -> None:
    args = _args()
    args.matched_count = 99

    class FakeService:
        def __init__(self, _repo) -> None:
            pass

        def publishing_context(self, _production_run_id: int):
            return {
                "master_account_id": "account-1",
                "scheme_id": "scheme-1",
                "bilibili_mid": "mid-1",
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def fetch_browser_pending(self, _backfill_id: str):
            return {
                "account_id": "account-1",
                "scheme_id": "scheme-1",
                "video_owner_mid": "mid-1",
                "production_run_id": "7",
                "status": "partial",
                "matched_count": 8,
                "unresolved_count": 0,
                "browser_pending": [],
            }

    monkeypatch.setattr(cli, "_init", lambda: (object(), object(), object(), object()))
    monkeypatch.setattr(production_history, "ProductionHistoryService", FakeService)
    monkeypatch.setattr(blue_link_backfill, "MasterBlueLinkBackfillClient", FakeClient)

    with pytest.raises(ValueError, match="与 Master 当前结果不一致"):
        cli.cmd_record_blue_link_backfill(args)
