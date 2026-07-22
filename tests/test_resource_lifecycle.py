from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bworkflow_sql.db import Database
from bworkflow_sql.resource_lifecycle import (
    assess_cleanup_candidates,
    audit_project_resources,
    delete_cleanup_batch,
    list_resource_state_events,
    prepare_cleanup_batch,
    reconcile_project_resources,
    register_cleanup_candidate,
    register_completed_product_image_job,
)
from bworkflow_sql.cli import build_parser


NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


def _seed_project(tmp_path: Path, monkeypatch):
    import bworkflow_sql.resource_lifecycle as lifecycle

    workspace = tmp_path / "workspace"
    monkeypatch.setattr(lifecycle, "INTERNAL_WORKSPACE_ROOT", workspace)
    image_root = tmp_path / "images"
    voice_root = tmp_path / "voices"
    video_root = tmp_path / "videos"
    spoken_path = tmp_path / "spoken" / "oven" / "7month-rongrong.md"
    spoken_path.parent.mkdir(parents=True)
    spoken_path.write_text("current spoken copy", encoding="utf-8")

    db = Database(tmp_path / "resource.db")
    project_id = db.upsert_project(
        {
            "name": "home-oven",
            "spoken_md_path": str(spoken_path),
            "image_root": str(image_root),
            "voice_root": str(voice_root),
            "video_root": str(video_root),
            "output_root": str(workspace),
        }
    )
    ready = voice_root / "home-oven" / "rongrong" / "ready.mp3"
    stale = voice_root / "home-oven" / "rongrong" / "stale.mp3"
    unbound_video = video_root / "home-oven" / "old-roll-b.mp4"
    for path, payload in (
        (ready, b"ready"),
        (stale, b"stale"),
        (unbound_video, b"video"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    missing = voice_root / "home-oven" / "rongrong" / "missing.mp3"
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, asset_type, account_label, path, status, source_kind, created_at, updated_at)
            VALUES
                (?, 'voice', 'rongrong', ?, 'ready', 'generated', 'now', 'now'),
                (?, 'voice', 'rongrong', ?, 'stale', 'generated', 'now', 'now'),
                (?, 'voice', 'rongrong', ?, 'expired', 'generated', 'now', 'now')
            """,
            (project_id, str(ready), project_id, str(stale), project_id, str(missing)),
        )
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, body, active, created_at, updated_at)
            VALUES (?, 'intro', 'old intro', 0, 'now', 'now')
            """,
            (project_id,),
        )

    old_job = workspace / "product-image-jobs" / f"project-{project_id}" / "legacy-job"
    old_job.mkdir(parents=True)
    (old_job / "render-package.json").write_text("{}", encoding="utf-8")
    old_time = (NOW - timedelta(days=3)).timestamp()
    os.utime(old_job, (old_time, old_time))
    return db, project_id, spoken_path, ready, stale, missing, unbound_video, old_job


def _classification_for(result: dict, path: Path) -> str:
    return next(
        item["classification"]
        for item in result["items"]
        if Path(item["path"]) == path
    )


def test_audit_is_conservative_when_pipeline_is_missing(tmp_path: Path, monkeypatch):
    db, project_id, _spoken, ready, stale, missing, unbound_video, old_job = _seed_project(
        tmp_path, monkeypatch
    )

    result = audit_project_resources(db, project_id=project_id, now=NOW)

    assert result["mode"] == "read_only_audit"
    assert result["pipeline"]["exists"] is False
    assert result["pipeline"]["asset_cleanup_suspended"] is True
    assert result["metadata"]["inactive_script_blocks"] == 1
    assert _classification_for(result, ready) == "current"
    assert _classification_for(result, stale) == "uncertain"
    assert _classification_for(result, missing) == "metadata_only"
    assert _classification_for(result, unbound_video) == "uncertain"
    assert _classification_for(result, old_job) == "reclaimable"


def test_pipeline_reference_protects_inactive_generated_asset(tmp_path: Path, monkeypatch):
    db, project_id, spoken, _ready, stale, _missing, _video, _job = _seed_project(
        tmp_path, monkeypatch
    )
    pipeline = spoken.parent / ".pipeline.json"
    pipeline.write_text('{"current_voice": "' + str(stale).replace("\\", "\\\\") + '"}', encoding="utf-8")

    result = audit_project_resources(db, project_id=project_id, now=NOW)

    assert result["pipeline"]["exists"] is True
    assert _classification_for(result, stale) == "current"


def test_reconcile_registers_legacy_job_without_deleting_it(tmp_path: Path, monkeypatch):
    db, project_id, _spoken, _ready, _stale, _missing, _video, old_job = _seed_project(
        tmp_path, monkeypatch
    )

    result = reconcile_project_resources(db, project_id=project_id, now=NOW)
    row = db.fetchone(
        "SELECT resource_kind, status, path FROM resource_cleanup_candidates WHERE path=?",
        (str(old_job),),
    )

    assert result["mode"] == "reconcile_candidates_only"
    assert result["registered_count"] == 1
    assert result["summary"]["cleanup_candidate"]["count"] == 1
    assert row["resource_kind"] == "product_image_job"
    assert row["status"] == "pending"
    assert old_job.is_dir()
    assert result["corrected_missing_binding_count"] == 0
    assert result["note"].startswith("No files were moved or deleted.")


def test_reconcile_corrects_ready_binding_when_file_is_missing(tmp_path: Path, monkeypatch):
    db, project_id, _spoken, _ready, _stale, missing, _video, _job = _seed_project(
        tmp_path, monkeypatch
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE asset_bindings SET status='ready' WHERE project_id=? AND path=?",
            (project_id, str(missing)),
        )

    result = reconcile_project_resources(db, project_id=project_id, now=NOW)
    corrected = db.fetchone(
        "SELECT status FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(missing)),
    )
    event = db.fetchone(
        """
        SELECT previous_state, new_state, reason, source
        FROM resource_state_events
        WHERE project_id=? AND path=? AND new_state='missing'
        """,
        (project_id, str(missing)),
    )

    assert result["corrected_missing_binding_count"] == 1
    assert corrected["status"] == "missing"
    assert dict(event) == {
        "previous_state": "ready",
        "new_state": "missing",
        "reason": "ready_binding_file_missing",
        "source": "resource_reconcile",
    }


def test_completed_product_image_job_is_registered_with_delayed_eligibility(
    tmp_path: Path,
):
    db = Database(tmp_path / "completed-job.db")
    project_id = db.upsert_project({"name": "oven"})
    package = tmp_path / "jobs" / "batch-1" / "render-package.json"
    package.parent.mkdir(parents=True)
    package.write_text("{}", encoding="utf-8")

    register_completed_product_image_job(
        db,
        project_id=project_id,
        package_path=package,
        now=NOW,
    )
    row = db.fetchone(
        "SELECT resource_kind, status, eligible_at FROM resource_cleanup_candidates WHERE path=?",
        (str(package.parent),),
    )

    assert row["resource_kind"] == "product_image_job"
    assert row["status"] == "pending"
    assert row["eligible_at"] == "2026-07-27T08:00:00Z"
    event = db.fetchone(
        """
        SELECT resource_kind, new_state, reason, path
        FROM resource_state_events
        WHERE project_id=?
        """,
        (project_id,),
    )
    assert dict(event) == {
        "resource_kind": "product_image_job",
        "new_state": "pending",
        "reason": "render_completed",
        "path": str(package.parent),
    }


def test_resource_lifecycle_cli_commands_are_parseable():
    parser = build_parser()

    audit = parser.parse_args(["resource-audit", "9"])
    reconcile = parser.parse_args(
        ["resource-reconcile", "9", "--pipeline", "C:/work/.pipeline.json"]
    )
    listing = parser.parse_args(
        ["resource-cleanup-list", "9", "--account", "荣荣", "--kind", "asset_voice"]
    )
    plan = parser.parse_args(
        ["resource-cleanup-plan", "9", "--pipeline", "C:/work/.pipeline.json"]
    )
    delete = parser.parse_args(
        [
            "resource-cleanup-delete",
            "--batch-id",
            "cleanup-1",
            "--confirm",
            "token-1",
            "--confirmed-by",
            "user",
        ]
    )
    history = parser.parse_args(
        ["resource-history", "9", "--kind", "voice", "--state", "expired", "--limit", "50"]
    )

    assert audit.project_id == 9
    assert reconcile.pipeline == "C:/work/.pipeline.json"
    assert listing.account == "荣荣"
    assert listing.kind == "asset_voice"
    assert plan.project_id == 9
    assert delete.batch_id == "cleanup-1"
    assert delete.confirm == "token-1"
    assert history.kind == "voice"
    assert history.state == "expired"
    assert history.limit == 50


def test_resource_history_filters_append_only_events(tmp_path: Path):
    db = Database(tmp_path / "resource-history.db")
    project_id = db.upsert_project({"name": "oven"})
    with db.connect() as conn:
        for state, account in (("created", "荣荣"), ("expired", "荣荣"), ("expired", "知了")):
            conn.execute(
                """
                INSERT INTO resource_state_events
                    (project_id, resource_kind, resource_key, new_state,
                     account_label, details_json, created_at)
                VALUES (?, 'voice', ?, ?, ?, '{"source":"test"}', 'now')
                """,
                (project_id, f"voice:{state}:{account}", state, account),
            )

    result = list_resource_state_events(
        db,
        project_id=project_id,
        resource_kind="voice",
        account_label="荣荣",
        new_state="expired",
        limit=50,
    )

    assert result["count"] == 1
    assert result["events"][0]["new_state"] == "expired"
    assert result["events"][0]["account_label"] == "荣荣"
    assert result["events"][0]["details"] == {"source": "test"}


def test_cleanup_assessment_requires_pipeline_for_inactive_assets(tmp_path: Path, monkeypatch):
    db, project_id, _spoken, _ready, stale, _missing, _video, _job = _seed_project(
        tmp_path, monkeypatch
    )
    register_cleanup_candidate(
        db,
        project_id=project_id,
        resource_kind="asset_voice",
        path=stale,
        reason="script_changed",
        eligible_at=NOW - timedelta(days=1),
    )

    result = assess_cleanup_candidates(db, project_id=project_id, now=NOW)

    assert result["summary"] == {
        "ready_count": 0,
        "ready_size_bytes": 0,
        "blocked_count": 1,
    }
    assert result["blocked"][0]["blocked_reason"] == (
        "pipeline_missing_cannot_prove_asset_is_unreferenced"
    )


def test_cleanup_assessment_returns_fingerprinted_asset_after_all_gates(
    tmp_path: Path,
    monkeypatch,
):
    db, project_id, spoken, _ready, stale, _missing, _video, _job = _seed_project(
        tmp_path, monkeypatch
    )
    pipeline = spoken.parent / ".pipeline.json"
    pipeline.write_text("{}", encoding="utf-8")
    register_cleanup_candidate(
        db,
        project_id=project_id,
        resource_kind="asset_voice",
        path=stale,
        reason="script_changed",
        eligible_at=NOW - timedelta(days=1),
    )

    result = assess_cleanup_candidates(db, project_id=project_id, now=NOW)

    assert result["summary"]["ready_count"] == 1
    assert result["blocked"] == []
    assert result["ready"][0]["entry_kind"] == "file"
    assert result["ready"][0]["size_bytes"] == len(b"stale")
    assert len(result["ready"][0]["fingerprint"]) == 64


def test_cleanup_assessment_rejects_currently_referenced_candidate(tmp_path: Path, monkeypatch):
    db, project_id, spoken, _ready, stale, _missing, _video, _job = _seed_project(
        tmp_path, monkeypatch
    )
    pipeline = spoken.parent / ".pipeline.json"
    pipeline.write_text(
        '{"current_voice": "' + str(stale).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )
    register_cleanup_candidate(
        db,
        project_id=project_id,
        resource_kind="asset_voice",
        path=stale,
        reason="script_changed",
        eligible_at=NOW - timedelta(days=1),
    )

    result = assess_cleanup_candidates(db, project_id=project_id, now=NOW)

    assert result["ready"] == []
    assert result["blocked"][0]["blocked_reason"] == "current_classification_current"


def test_cleanup_assessment_rejects_candidate_outside_managed_roots(tmp_path: Path, monkeypatch):
    db, project_id, spoken, _ready, _stale, _missing, _video, _job = _seed_project(
        tmp_path, monkeypatch
    )
    pipeline = spoken.parent / ".pipeline.json"
    pipeline.write_text("{}", encoding="utf-8")
    external = tmp_path / "outside-managed-roots.mp3"
    external.write_bytes(b"external")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, asset_type, account_label, path, status, source_kind,
                 created_at, updated_at)
            VALUES (?, 'voice', 'rongrong', ?, 'stale', 'generated', 'now', 'now')
            """,
            (project_id, str(external)),
        )
    register_cleanup_candidate(
        db,
        project_id=project_id,
        resource_kind="asset_voice",
        path=external,
        reason="script_changed",
        eligible_at=NOW - timedelta(days=1),
    )

    result = assess_cleanup_candidates(db, project_id=project_id, now=NOW)

    outside = next(item for item in result["blocked"] if Path(item["path"]) == external)
    assert outside["blocked_reason"] == "path_outside_managed_roots"
    assert external.exists()


def _prepare_stale_voice_batch(tmp_path: Path, monkeypatch):
    db, project_id, spoken, _ready, stale, _missing, _video, _job = _seed_project(
        tmp_path, monkeypatch
    )
    pipeline = spoken.parent / ".pipeline.json"
    pipeline.write_text("{}", encoding="utf-8")
    register_cleanup_candidate(
        db,
        project_id=project_id,
        resource_kind="asset_voice",
        path=stale,
        reason="script_changed",
        eligible_at=NOW - timedelta(days=1),
    )
    prepared = prepare_cleanup_batch(db, project_id=project_id, now=NOW)
    return db, project_id, stale, prepared


def test_cleanup_batch_token_is_not_stored_in_plaintext(tmp_path: Path, monkeypatch):
    db, _project_id, stale, prepared = _prepare_stale_voice_batch(tmp_path, monkeypatch)
    batch = prepared["batch"]
    stored = db.fetchone(
        "SELECT confirmation_token_hash, status FROM resource_cleanup_batches WHERE id=?",
        (batch["id"],),
    )

    assert batch["status"] == "prepared"
    assert batch["candidate_count"] == 1
    assert batch["total_size_bytes"] == len(b"stale")
    assert stale.exists()
    assert stored["status"] == "prepared"
    assert stored["confirmation_token_hash"] != batch["confirmation_token"]
    assert len(stored["confirmation_token_hash"]) == 64


def test_cleanup_delete_rejects_wrong_token_without_touching_file(tmp_path: Path, monkeypatch):
    db, _project_id, stale, prepared = _prepare_stale_voice_batch(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="token does not match"):
        delete_cleanup_batch(
            db,
            batch_id=prepared["batch"]["id"],
            confirmation_token="wrong-token",
            now=NOW,
        )

    assert stale.exists()


def test_cleanup_delete_rejects_changed_snapshot_without_deleting(tmp_path: Path, monkeypatch):
    db, _project_id, stale, prepared = _prepare_stale_voice_batch(tmp_path, monkeypatch)
    stale.write_bytes(b"changed-after-confirmation-plan")

    result = delete_cleanup_batch(
        db,
        batch_id=prepared["batch"]["id"],
        confirmation_token=prepared["batch"]["confirmation_token"],
        now=NOW,
    )

    assert result["ok"] is False
    assert result["status"] == "stale"
    assert result["deleted_count"] == 0
    assert stale.read_bytes() == b"changed-after-confirmation-plan"


def test_cleanup_delete_permanently_removes_file_and_keeps_tombstone(
    tmp_path: Path,
    monkeypatch,
):
    db, _project_id, stale, prepared = _prepare_stale_voice_batch(tmp_path, monkeypatch)

    result = delete_cleanup_batch(
        db,
        batch_id=prepared["batch"]["id"],
        confirmation_token=prepared["batch"]["confirmation_token"],
        confirmed_by="user",
        now=NOW,
    )
    candidate = db.fetchone(
        "SELECT status, path, reason, resolved_at FROM resource_cleanup_candidates WHERE path=?",
        (str(stale),),
    )
    event = db.fetchone(
        """
        SELECT previous_state, new_state, source, details_json
        FROM resource_state_events
        WHERE path=? AND new_state='purged'
        """,
        (str(stale),),
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["deleted_count"] == 1
    assert not stale.exists()
    assert candidate["status"] == "purged"
    assert candidate["path"] == str(stale)
    assert candidate["reason"] == "script_changed"
    assert candidate["resolved_at"]
    assert event["previous_state"] == "pending"
    assert event["new_state"] == "purged"
    assert event["source"] == "resource_cleanup_delete"
    assert prepared["batch"]["id"] in event["details_json"]


def test_cleanup_delete_permanently_removes_confirmed_product_job_directory(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.resource_lifecycle as lifecycle

    workspace = tmp_path / "workspace"
    monkeypatch.setattr(lifecycle, "INTERNAL_WORKSPACE_ROOT", workspace)
    db = Database(tmp_path / "job-delete.db")
    project_id = db.upsert_project({"name": "oven"})
    package = workspace / "product-image-jobs" / f"project-{project_id}" / "old-job" / "render-package.json"
    package.parent.mkdir(parents=True)
    package.write_text("{}", encoding="utf-8")
    register_completed_product_image_job(
        db,
        project_id=project_id,
        package_path=package,
        now=NOW - timedelta(days=10),
    )
    prepared = prepare_cleanup_batch(db, project_id=project_id, now=NOW)

    result = delete_cleanup_batch(
        db,
        batch_id=prepared["batch"]["id"],
        confirmation_token=prepared["batch"]["confirmation_token"],
        now=NOW,
    )

    assert result["ok"] is True
    assert result["deleted_count"] == 1
    assert not package.parent.exists()
