import json
import hashlib
from pathlib import Path

import pytest
from datetime import datetime

from bworkflow_sql.db import Database
from bworkflow_sql.production_history import ProductionHistoryService
from bworkflow_sql.repositories import Repository
from bworkflow_sql.production_recipe import build_production_recipe, write_production_recipe


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _service(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    project_id = db.upsert_project({"name": "数码-桌面音响", "scheme_id": "s1", "scheme_name": "主方案"})
    return db, project_id, ProductionHistoryService(Repository(db))


def _manifest(tmp_path: Path, project_id: int, *, acceptance_mode: str = "quick") -> Path:
    mp4 = tmp_path / "完整成片.mp4"
    mp4.write_bytes(b"mp4")
    path = tmp_path / "run.json"
    path.write_text(json.dumps({
        "kind": "bworkflow.final_video_run", "createdAt": "2026-07-12T12:00:00",
        "project": {"id": project_id, "account": "小博"},
        "selection": {"product_card_template_id": "muban-xiaobo-3", "acceptance_mode": acceptance_mode},
        "outputs": {"full_mp4": str(mp4)},
        "reports": {"verification": {"full_ffprobe": {"duration": 60}}},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_confirm_is_idempotent_and_history_recommends_unused_template(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    manifest = _manifest(tmp_path, project_id)
    first = service.confirm(project_id, run_manifest_path=manifest)
    second = service.confirm(project_id, run_manifest_path=manifest)
    history = service.history(project_id, account_label="小博")

    assert first["created"] is True
    assert second["created"] is False
    assert history["used_template_ids"] == ["muban-xiaobo-3"]
    assert history["recommended_template"]["id"] != "muban-xiaobo-3"
    db.close()


def test_publishing_context_and_partial_blue_link_backfill_use_stable_ids(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    repo = service.repository
    repo.upsert_account({"label": "小博", "account_id": "xiaobo"})
    production = service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id))["production"]
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(json.dumps({"phases": {"publishing": {"status": "done"}}}), encoding="utf-8")

    context = service.publishing_context(production["id"])
    result = service.record_blue_link_backfill(
        production["id"],
        pipeline_path=pipeline,
        published_video_url="https://www.bilibili.com/video/BV1test",
        bvid="BV1test",
        aid="123",
        video_owner_mid=context["bilibili_mid"],
        backfill_id="backfill-1",
        status="partial",
        matched_count=18,
        unresolved_count=3,
        browser_pending_count=1,
        browser_suspended_count=1,
        title_candidate_count=1,
    )
    payload = json.loads(pipeline.read_text(encoding="utf-8"))

    assert context["master_account_id"] == "5fe6305b-c1ca-4ee4-bfd7-9407bd4e5302"
    assert context["scheme_id"] == "s1"
    assert result["production"]["blue_link_matched_count"] == 18
    assert payload["current_phase"] == "blue_link_backfill"
    assert payload["phases"]["blue_link_backfill"]["unresolved_count"] == 3
    assert payload["phases"]["blue_link_backfill"]["browser_suspended_count"] == 1
    assert payload["phases"]["blue_link_backfill"]["title_candidate_count"] == 1
    db.close()


def test_unaccepted_render_cannot_enter_formal_history(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    with pytest.raises(ValueError, match="未执行验收"):
        service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id, acceptance_mode="none"))
    assert service.history(project_id, account_label="小博")["history"] == []
    db.close()


def test_confirm_can_record_an_explicit_post_edited_final_mp4(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    manifest = _manifest(tmp_path, project_id)
    edited = tmp_path / "后续剪辑发布版.mp4"
    edited.write_bytes(b"edited-final")

    result = service.confirm(project_id, run_manifest_path=manifest, final_path=edited)

    assert result["production"]["full_mp4_path"] == str(edited.resolve())
    assert result["production"]["original_full_mp4_path"] != str(edited.resolve())
    assert result["production"]["full_mp4_sha256"] == _file_sha256(edited)
    db.close()


def test_external_edit_is_explicit_and_same_manifest_cannot_silently_change_final(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    manifest = _manifest(tmp_path, project_id)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    generated = Path(payload["outputs"]["full_mp4"])
    payload["file_fingerprints"] = [{"role": "full_mp4", "sha256": _file_sha256(generated)}]
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    edited = tmp_path / "edited.mp4"
    edited.write_bytes(b"edited")

    result = service.confirm(project_id, run_manifest_path=manifest, final_path=edited)
    assert result["production"]["recipe_status"] == "external_edit"
    assert service.rerender_preflight(result["production"]["id"])["rerenderable"] is False

    second_edit = tmp_path / "edited-again.mp4"
    second_edit.write_bytes(b"different")
    with pytest.raises(ValueError, match="不能静默覆盖"):
        service.confirm(project_id, run_manifest_path=manifest, final_path=second_edit)
    db.close()


def test_frozen_rerender_creates_candidate_without_demoting_published_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db, project_id, service = _service(tmp_path)
    manifest = _manifest(tmp_path, project_id)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    generated = Path(payload["outputs"]["full_mp4"])
    job_package = tmp_path / "job" / "render-package.json"
    job_package.parent.mkdir()
    job_package.write_text(json.dumps({"schemaVersion": "1.0.0", "segments": [], "assets": {}}), encoding="utf-8")
    source_package = tmp_path / "source-package.json"
    source_package.write_text("{}", encoding="utf-8")
    recipe = build_production_recipe(
        job_package_path=job_package,
        source_package_path=source_package,
        cutme_result={"schema_version": "1.0.0", "operation": "render_final", "artifacts": {}},
    )
    recipe_path = write_production_recipe(recipe, tmp_path / "recipe.json")
    payload["file_fingerprints"] = [{"role": "full_mp4", "sha256": _file_sha256(generated)}]
    payload["recipe"] = {"path": str(recipe_path), "sha256": recipe["recipeSha256"]}
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    confirmed = service.confirm(project_id, run_manifest_path=manifest)["production"]
    assert confirmed["recipe_status"] == "reproducible"

    delivery = tmp_path / "delivery"
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(
        json.dumps(
            {
                "current_phase": "done",
                "output_dir": str(delivery),
                "project": {"id": project_id},
                "phases": {"assembly": {}, "publishing": {"status": "done"}},
            }
        ),
        encoding="utf-8",
    )

    class FakeAdapter:
        def render_final(self, package_path, *, output_path, cache_dir=None):
            assert cache_dir == tmp_path / "workspace" / "project-1" / "render" / "final-video-cache"
            output = Path(output_path)
            output.write_bytes(b"rerendered")
            return {"artifacts": {"output_path": str(output)}}

    monkeypatch.setattr(
        "bworkflow_sql.production_history._probe_video",
        lambda path: {"duration": 10.0, "size": path.stat().st_size, "has_video": True, "has_audio": True},
    )
    monkeypatch.setattr(
        "bworkflow_sql.production_history.INTERNAL_WORKSPACE_ROOT",
        tmp_path / "workspace",
    )
    result = service.rerender(
        confirmed["id"], pipeline_path=pipeline, cutme_adapter=FakeAdapter()
    )

    pipeline_payload = json.loads(pipeline.read_text(encoding="utf-8"))
    assert result["status"] == "candidate_generated"
    assert pipeline_payload["current_phase"] == "done"
    assert pipeline_payload["phases"]["publishing"]["status"] == "done"
    assert pipeline_payload["phases"]["assembly"]["pending_candidate"]["mp4_path"] == result["candidate_mp4"]
    assert Path(confirmed["full_mp4_path"]).is_file()
    db.close()


def test_history_aggregates_same_category_across_projects(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id))
    second_id = db.upsert_project({"name": "数码-桌面音响", "scheme_id": "s2", "scheme_name": "副方案"})

    history = service.history(second_id, account_label="小博")

    assert len(history["history"]) == 1
    assert history["history"][0]["project_id"] == project_id
    db.close()


def test_complete_publishing_moves_file_and_updates_existing_pipeline_phase(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    confirmed = service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id))
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(json.dumps({
        "current_phase": "publishing",
        "phases": {"assembly": {}, "publishing": {"status": "pending"}},
        "paths": {},
    }, ensure_ascii=False), encoding="utf-8")
    archive_dir = tmp_path / "已发布" / "0712-桌面音响-小博"

    result = service.complete_publishing(
        confirmed["production"]["id"],
        pipeline_path=pipeline,
        archive_dir=archive_dir,
    )

    target = archive_dir / "完整成片.mp4"
    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    assert result["moved"] is True
    assert target.is_file()
    assert result["production"]["publish_status"] == "archived"
    assert result["production"]["full_mp4_path"] == str(target.resolve())
    assert payload["current_phase"] == "blue_link_backfill"
    assert payload["phases"]["publishing"]["status"] == "done"
    assert payload["phases"]["blue_link_backfill"]["status"] == "pending"
    assert payload["paths"]["final_mp4"] == str(target.resolve())

    again = service.complete_publishing(
        confirmed["production"]["id"],
        pipeline_path=pipeline,
        archive_dir=archive_dir,
    )
    assert again["moved"] is False
    db.close()


def test_complete_publishing_validates_before_move(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    confirmed = service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id))
    source = Path(confirmed["production"]["full_mp4_path"])
    source.write_bytes(b"changed-after-confirm")
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(json.dumps({"phases": {}, "paths": {}}), encoding="utf-8")
    archive_dir = tmp_path / "published"

    with pytest.raises(ValueError, match="不一致"):
        service.complete_publishing(
            confirmed["production"]["id"], pipeline_path=pipeline, archive_dir=archive_dir
        )
    assert source.is_file()
    assert not (archive_dir / source.name).exists()
    db.close()


def test_complete_publishing_relinks_a_manually_moved_matching_file(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    confirmed = service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id))
    original = Path(confirmed["production"]["full_mp4_path"])
    moved = tmp_path / "手工已发布" / original.name
    moved.parent.mkdir()
    original.replace(moved)
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(json.dumps({"phases": {}, "paths": {}}, ensure_ascii=False), encoding="utf-8")

    result = service.complete_publishing(
        confirmed["production"]["id"],
        pipeline_path=pipeline,
        current_path=moved,
    )

    assert result["moved"] is False
    assert result["production"]["full_mp4_path"] == str(moved.resolve())
    db.close()


def test_complete_publishing_defaults_to_existing_current_month_directory(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    confirmed = service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id))
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(json.dumps({"phases": {}, "paths": {}}, ensure_ascii=False), encoding="utf-8")
    published_root = tmp_path / "已发布视频"
    month_dir = published_root / "7月"
    month_dir.mkdir(parents=True)

    result = service.complete_publishing(
        confirmed["production"]["id"],
        pipeline_path=pipeline,
        published_root=published_root,
        now=datetime(2026, 7, 12),
    )

    assert Path(result["production"]["full_mp4_path"]).parent == month_dir.resolve()
    db.close()


def test_complete_publishing_defaults_to_root_when_current_month_directory_is_missing(tmp_path: Path):
    db, project_id, service = _service(tmp_path)
    confirmed = service.confirm(project_id, run_manifest_path=_manifest(tmp_path, project_id))
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(json.dumps({"phases": {}, "paths": {}}, ensure_ascii=False), encoding="utf-8")
    published_root = tmp_path / "已发布视频"
    published_root.mkdir()

    result = service.complete_publishing(
        confirmed["production"]["id"],
        pipeline_path=pipeline,
        published_root=published_root,
        now=datetime(2026, 8, 1),
    )

    assert Path(result["production"]["full_mp4_path"]).parent == published_root.resolve()
    assert not (published_root / "8月").exists()
    db.close()
