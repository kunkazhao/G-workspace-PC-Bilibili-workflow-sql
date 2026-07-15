import json
from pathlib import Path

from bworkflow_sql.artifact_approvals import confirm_intro_video, write_production_confirmation


def test_confirm_intro_video_writes_bound_approval_atomically(tmp_path: Path) -> None:
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(json.dumps({"paths": {}, "phases": {}}), encoding="utf-8")
    intro = tmp_path / "intro.mp4"
    intro.write_bytes(b"intro")
    source_plan = tmp_path / "source-intro-plan.json"
    source_plan.write_text("{}", encoding="utf-8")

    approval = confirm_intro_video(
        pipeline,
        intro,
        approved_at="2026-07-15T00:00:00Z",
        source_revision="sha256:source",
        source_plan_path=source_plan,
    )

    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    assert approval["artifact_type"] == "intro_video"
    assert approval["sha256"].startswith("sha256:")
    assert payload["artifact_approvals"]["intro_video"] == approval
    assert payload["episode_id"].startswith("episode:sha256:")
    assert payload["phases"]["intro_video"]["accepted"] is True
    assert payload["phases"]["intro_video"]["source_intro_plan_path"] == str(source_plan.resolve())
    assert list(tmp_path.glob(".*.tmp")) == []


def test_production_confirmation_binds_mp4_to_manifest_revision(tmp_path: Path) -> None:
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(
        json.dumps(
            {
                "phases": {
                    "assembly": {
                        "pending_candidate": {"run_manifest_path": str(tmp_path / "run.json")}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "run.json"
    manifest.write_text("{}", encoding="utf-8")
    video = tmp_path / "final.mp4"
    video.write_bytes(b"final")
    production = {
        "id": 7,
        "run_manifest_path": str(manifest),
        "full_mp4_path": str(video),
        "confirmed_at": "2026-07-15T00:00:00Z",
    }

    approval = write_production_confirmation(pipeline, production)

    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    assert approval["source_revision"].startswith("sha256:")
    assert payload["production_confirmation"]["production_run_id"] == 7
    assert payload["artifact_approvals"]["full_mp4"] == approval
    assert "pending_candidate" not in payload["phases"]["assembly"]
