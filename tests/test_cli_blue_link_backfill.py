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


def test_backfill_report_cli_requires_task_and_workspace() -> None:
    args = cli.build_parser().parse_args(
        [
            "blue-link-backfill-report",
            "job-1",
            "--workspace-id",
            "workspace-1",
        ]
    )

    assert args.backfill_id == "job-1"
    assert args.workspace_id == "workspace-1"


def test_title_confirmation_cli_accepts_one_batch_decision_file(tmp_path, monkeypatch) -> None:
    decision_file = tmp_path / "decisions.json"
    decision_file.write_text(
        '{"expected_scan_revision":3,"decision_batch_id":"batch-1","decisions":[{"source_link":"https://b23.tv/a","action":"reject"}]}',
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(
        [
            "confirm-blue-link-title-candidates",
            "job-1",
            "--workspace-id",
            "workspace-1",
            "--decision-file",
            str(decision_file),
        ]
    )
    captured = {}
    monkeypatch.setattr(
        blue_link_backfill,
        "confirm_blue_link_title_candidates",
        lambda backfill_id, decisions, **kwargs: captured.update(
            {"backfill_id": backfill_id, "decisions": decisions, **kwargs}
        )
        or {"status": "partial"},
    )
    monkeypatch.setattr(cli, "_json_out", lambda _payload: None)

    cli.cmd_confirm_blue_link_title_candidates(args)

    assert captured["backfill_id"] == "job-1"
    assert captured["decisions"] == [
        {"source_link": "https://b23.tv/a", "action": "reject"}
    ]
    assert captured["expected_scan_revision"] == 3
    assert captured["decision_batch_id"] == "batch-1"


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
                "unresolved_count": 5,
                "browser_pending_count": 1,
                "browser_deferred_count": 1,
                "browser_suspended_count": 1,
                "title_candidate_count": 1,
                "master_data_pending_count": 1,
                "browser_pending": [],
            }

    monkeypatch.setattr(cli, "_init", lambda: (object(), object(), object(), object()))
    monkeypatch.setattr(cli, "_json_out", lambda _payload: None)
    monkeypatch.setattr(production_history, "ProductionHistoryService", FakeService)
    monkeypatch.setattr(blue_link_backfill, "MasterBlueLinkBackfillClient", FakeClient)

    cli.cmd_record_blue_link_backfill(_args())

    assert recorded["matched_count"] == 8
    assert recorded["unresolved_count"] == 5
    assert recorded["browser_suspended_count"] == 1
    assert recorded["title_candidate_count"] == 1
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
