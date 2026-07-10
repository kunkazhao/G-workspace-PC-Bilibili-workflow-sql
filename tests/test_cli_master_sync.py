from argparse import Namespace

from bworkflow_sql import cli


def test_cli_master_sync_keeps_counts_and_exposes_snapshot_identity(monkeypatch):
    captured = []

    class FakeSync:
        def sync_master_scheme(self, project_id):
            assert project_id == 23
            return {
                "snapshot_id": "sha256:" + "a" * 64,
                "change_count": 4,
                "added": [{"uid": "A"}],
                "updated": [{"uid": "B"}],
                "reactivated": [{"uid": "C"}],
                "removed": [{"uid": "D"}],
            }

    monkeypatch.setattr(cli, "_init", lambda: (None, None, FakeSync(), None))
    monkeypatch.setattr(cli, "_json_out", captured.append)

    cli.cmd_sync(Namespace(project_id=23, step="master", asset_type=None))

    assert captured == [
        {
            "ok": True,
            "master": {
                "snapshot_id": "sha256:" + "a" * 64,
                "change_count": 4,
                "added": 1,
                "updated": 1,
                "reactivated": 1,
                "removed": 1,
            },
        }
    ]
