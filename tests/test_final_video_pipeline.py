from __future__ import annotations

import json
from pathlib import Path

import pytest

from bworkflow_sql.cutme_adapter import CutMeAdapterError
from bworkflow_sql.final_video_pipeline import (
    _run_command,
    _run_dynamic_product_preflight as REAL_DYNAMIC_PREFLIGHT,
    run_final_video_pipeline,
)
from bworkflow_sql.phase7_selection import confirm_phase7_selection
from bworkflow_sql.artifact_approvals import confirm_intro_video


pytestmark = pytest.mark.usefixtures(
    "isolated_final_video_workspace",
    "ready_dynamic_preflight",
)


@pytest.fixture
def ready_dynamic_preflight(monkeypatch: pytest.MonkeyPatch):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "_run_dynamic_product_preflight",
        lambda workflow, **kwargs: {
            "ok": True,
            "status": "ready",
            "issues": [],
            "contexts": [],
            "media_readiness": {"selected_paths": {}},
            "snapshot_id": "test-snapshot",
        },
    )


def test_final_render_blocks_if_verified_video_changes_before_package_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    video = tmp_path / "P001.mp4"
    video.write_bytes(b"not-a-real-video")
    monkeypatch.setattr(
        pipeline_module,
        "_run_dynamic_product_preflight",
        lambda workflow, **kwargs: {
            "ok": True,
            "contexts": [],
            "media_readiness": {"selected_paths": {"P001": str(video)}},
        },
    )

    result = run_final_video_pipeline(
        object(),
        project_id=1,
        account_label="xiaobo",
        acceptance_mode="none",
        probe_video=lambda _path: {"ok": False, "reason": "decode_failed"},
    )

    assert result["ok"] is False
    assert result["stage"] == "media_readiness_snapshot"
    assert result["media_snapshot"]["issues"][0]["uid"] == "P001"


@pytest.fixture(autouse=True)
def fake_cutme_boundary(tmp_path: Path, monkeypatch):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    calls: list[tuple[Path, Path, Path]] = []

    class FakeCutMeAdapter:
        def __init__(self, *, cutme_root):
            self.cutme_root = Path(cutme_root)

        def render_final(self, package_path, *, output_path, cache_dir):
            source = Path(package_path).resolve()
            output = Path(output_path).resolve()
            cache = Path(cache_dir).resolve()
            calls.append((source, output, cache))
            job_package = (tmp_path / "job" / "render-package.json").resolve()
            job_package.parent.mkdir(parents=True, exist_ok=True)
            job_package.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"mp4")
            cache_manifest = cache / "clip-cache-manifest.json"
            cache_manifest.parent.mkdir(parents=True, exist_ok=True)
            cache_summary = {"segments_total": 1, "cache_hits": 1, "rendered": 0}
            cache_manifest.write_text(json.dumps({"summary": cache_summary}), encoding="utf-8")
            return {
                "schema_version": "1.0.0",
                "kind": "cutme.render_result",
                "operation": "render_final",
                "ok": True,
                "status": "succeeded",
                "artifacts": {
                    "source_package_path": str(source),
                    "job_package_path": str(job_package),
                    "output_path": str(output),
                    "cache_manifest_path": str(cache_manifest),
                },
                "cache": cache_summary,
                "timings": {"prepare_job_ms": 2, "render_ms": 8, "total_ms": 10},
                "error": None,
            }

    monkeypatch.setattr(pipeline_module, "CutMeAdapter", FakeCutMeAdapter)
    return calls


def test_dynamic_preflight_failure_has_no_render_or_cache_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    workspace = tmp_path / "workspace"
    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", workspace)
    render_root = workspace / "project-23" / "render"
    cache_manifest = tmp_path / "workspace" / "render-cache" / "clip-cache-v1" / "clip-cache-manifest.json"
    cache_manifest.parent.mkdir(parents=True)
    sentinel = b"sentinel-cache-bytes"
    cache_manifest.write_bytes(sentinel)
    before_paths = sorted(path.relative_to(render_root) for path in render_root.rglob("*"))
    calls = {"preflight": 0, "regenerate": 0, "prepare": 0, "cutme": 0}

    class FakeWorkflow:
        def dynamic_product_card_preflight(self, *args, **kwargs):
            calls["preflight"] += 1
            return {
                "ok": False,
                "status": "blocked",
                "error_code": "master_unavailable",
                "issues": [{"code": "invalid_product_price", "product_uid": "P001"}],
                "contexts": [],
                "snapshot_id": "snapshot-1",
            }

        def regenerate_product_card_images(self, *args, **kwargs):
            calls["regenerate"] += 1
            raise AssertionError("image regeneration must not run")

        def prepare_product_recommendation_output(self, *args, **kwargs):
            calls["prepare"] += 1
            raise AssertionError("package preparation must not run")

    class FakeCutMe:
        def render_final(self, *args, **kwargs):
            calls["cutme"] += 1
            raise AssertionError("CutMe must not run")

    monkeypatch.setattr(
        pipeline_module,
        "_run_dynamic_product_preflight",
        REAL_DYNAMIC_PREFLIGHT,
    )

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
        cutme_adapter=FakeCutMe(),
        acceptance_mode="none",
    )

    assert result["ok"] is False
    assert result["stage"] == "dynamic_product_preflight"
    assert result["error_code"] == "master_unavailable"
    assert calls == {"preflight": 1, "regenerate": 0, "prepare": 0, "cutme": 0}
    assert cache_manifest.read_bytes() == sentinel
    assert sorted(path.relative_to(render_root) for path in render_root.rglob("*")) == before_paths


def test_dynamic_preflight_failure_does_not_create_render_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    workspace = tmp_path / "workspace"
    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(
        pipeline_module,
        "_run_dynamic_product_preflight",
        REAL_DYNAMIC_PREFLIGHT,
    )

    result = run_final_video_pipeline(
        object(),
        project_id=24,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
        acceptance_mode="none",
    )

    assert result["stage"] == "dynamic_product_preflight"
    assert result["preflight"]["error_code"] == "dynamic_product_preflight_unavailable"
    assert not (workspace / "project-24" / "render").exists()


def test_dynamic_preflight_success_enters_existing_package_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    workspace = tmp_path / "workspace"
    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", workspace)
    calls = {"preflight": 0, "prepare": 0}
    frozen_contexts = [{"product_uid": "P001", "data_map": {"title": "Frozen"}}]
    captured_kwargs = {}

    class FakeWorkflow:
        def dynamic_product_card_preflight(self, *args, **kwargs):
            calls["preflight"] += 1
            return {
                "ok": True,
                    "issues": [],
                    "contexts": frozen_contexts,
                    "media_readiness": {"selected_paths": {}},
                    "snapshot_id": "snapshot-1",
            }

        def prepare_product_recommendation_output(self, *args, **kwargs):
            calls["prepare"] += 1
            captured_kwargs.update(kwargs)
            return {"ok": False, "missing": [{"kind": "expected-test-stop"}]}

    monkeypatch.setattr(
        pipeline_module,
        "_run_dynamic_product_preflight",
        REAL_DYNAMIC_PREFLIGHT,
    )

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=25,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
        acceptance_mode="none",
    )

    assert calls == {"preflight": 1, "prepare": 1}
    assert captured_kwargs["dynamic_product_contexts"] is frozen_contexts
    assert captured_kwargs["master_snapshot_id"] == "snapshot-1"
    assert result["stage"] == "render_package"
    assert (workspace / "project-25" / "render").is_dir()


@pytest.mark.parametrize(
    "business_code",
    ["master_unavailable", "invalid_product_card_template"],
)
def test_dynamic_preflight_preserves_business_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    business_code: str,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr(
        pipeline_module,
        "_run_dynamic_product_preflight",
        REAL_DYNAMIC_PREFLIGHT,
    )

    class FakeWorkflow:
        def dynamic_product_card_preflight(self, *args, **kwargs):
            return {"ok": False, "error_code": business_code, "issues": [], "contexts": []}

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=26,
        account_label="xiaobo",
        product_card_template_id="muban-test-1",
        acceptance_mode="none",
    )
    assert result["error_code"] == business_code


def test_dynamic_preflight_programming_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "_run_dynamic_product_preflight",
        REAL_DYNAMIC_PREFLIGHT,
    )

    class BrokenWorkflow:
        def dynamic_product_card_preflight(self, *args, **kwargs):
            raise AssertionError("programming bug")

    with pytest.raises(AssertionError, match="programming bug"):
        run_final_video_pipeline(
            BrokenWorkflow(),
            project_id=27,
            account_label="xiaobo",
            product_card_template_id="muban-test-1",
            acceptance_mode="none",
        )


def test_run_final_video_pipeline_uses_one_adapter_call_and_preserves_cutme_result(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    output_mp4 = tmp_path / "final.mp4"
    cache_manifest = tmp_path / "cache" / "clip-cache-manifest.json"
    pipeline_path = tmp_path / ".pipeline.json"
    intro_mp4 = tmp_path / "intro.mp4"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(
        json.dumps({"schemaVersion": "1.0.0", "segments": []}),
        encoding="utf-8",
    )
    pipeline_path.write_text("{}", encoding="utf-8")
    confirm_phase7_selection(
        pipeline_path,
        output_branch="final_mp4",
        account="小博",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="standard",
    )
    confirm_intro_video(pipeline_path, intro_mp4, approved_at="2026-07-19T00:00:00Z")
    class FakeWorkflow:
        def regenerate_product_card_images(self, *args, **kwargs):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, *args, **kwargs):
            return {
                "ok": True,
                "package_path": str(package_path),
                "next": {"target_mp4": str(output_mp4)},
            }

    class FakeCutMeAdapter:
        def __init__(self):
            self.calls: list[tuple[Path, Path, Path]] = []

        def render_final(self, package, *, output_path, cache_dir):
            self.calls.append((Path(package), Path(output_path), Path(cache_dir)))
            job_package_path.parent.mkdir(parents=True, exist_ok=True)
            job_package_path.write_text(package_path.read_text(encoding="utf-8"), encoding="utf-8")
            output_mp4.write_bytes(b"mp4")
            cache_manifest.parent.mkdir(parents=True, exist_ok=True)
            cache_manifest.write_text("{}", encoding="utf-8")
            return {
                "schema_version": "1.0.0",
                "kind": "cutme.render_result",
                "operation": "render_final",
                "ok": True,
                "status": "succeeded",
                "artifacts": {
                    "source_package_path": str(package_path),
                    "job_package_path": str(job_package_path),
                    "output_path": str(output_mp4),
                    "cache_manifest_path": str(cache_manifest),
                },
                "cache": {"segments_total": 3, "cache_hits": 2, "rendered": 1},
                "timings": {
                    "validate_source_ms": 5,
                    "prepare_job_ms": 20,
                    "render_ms": 4300,
                    "total_ms": 4325,
                },
                "error": None,
            }

    adapter = FakeCutMeAdapter()
    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        product_card_template_id="muban-xiaobo-1",
        package_output_path=package_path,
        output_path=output_mp4,
        pipeline_path=pipeline_path,
        intro_video_path=intro_mp4,
        intro_video_text="测试引言",
        acceptance_mode="none",
        cutme_root=tmp_path,
        cutme_adapter=adapter,
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("B-Workflow command runner must not invoke CutMe")
        ),
        probe_video=lambda path: {"duration": 1.0},
    )

    expected_cache_dir = tmp_path / "workspace" / "render-cache" / "clip-cache-v1"
    assert adapter.calls == [(package_path.resolve(), output_mp4.resolve(), expected_cache_dir)]
    assert result["job_package_path"] == str(job_package_path)
    assert result["output_mp4"] == str(output_mp4)
    assert result["cutme"]["clip_cache_manifest"] == str(cache_manifest)
    assert result["cutme"]["clip_cache"] == {
        "segments_total": 3,
        "cache_hits": 2,
        "rendered": 1,
    }
    assert result["cutme"]["result"]["operation"] == "render_final"
    assert result["cutme"]["timings"]["render_ms"] == 4300
    assert result["timings"]["total_ms"] != result["cutme"]["timings"]["total_ms"]
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["inputs"]["cutme_job_package_path"] == str(job_package_path)
    assert manifest["reports"]["clip_cache"]["cache_hits"] == 2
    assert manifest["reports"]["cutme_timings"]["prepare_job_ms"] == 20
    saved_pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    assert saved_pipeline["paths"]["job_package"] == str(job_package_path)
    assert saved_pipeline["phases"]["assembly"]["clip_cache"]["rendered"] == 1
    assert saved_pipeline["phases"]["assembly"]["cutme_timings"]["render_ms"] == 4300


def test_run_final_video_pipeline_cutme_failure_stops_before_post_processing_or_writeback(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    workspace = tmp_path / "workspace"
    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", workspace)
    package_path = tmp_path / "render-package.json"
    output_mp4 = tmp_path / "final.mp4"
    pipeline_path = tmp_path / ".pipeline.json"
    intro_mp4 = tmp_path / "intro.mp4"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(
        json.dumps({"schemaVersion": "1.0.0", "segments": []}),
        encoding="utf-8",
    )
    original_pipeline = '{"current_phase":"intro_video"}'
    pipeline_path.write_text(original_pipeline, encoding="utf-8")
    confirm_phase7_selection(
        pipeline_path,
        output_branch="final_mp4",
        account="小博",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="standard",
    )
    confirm_intro_video(pipeline_path, intro_mp4, approved_at="2026-07-19T00:00:00Z")
    confirmed_pipeline = pipeline_path.read_text(encoding="utf-8")

    class FakeWorkflow:
        def regenerate_product_card_images(self, *args, **kwargs):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, *args, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {}}

    class FailingCutMeAdapter:
        def __init__(self):
            self.calls = 0

        def render_final(self, *args, **kwargs):
            self.calls += 1
            raise CutMeAdapterError("cutme_render_failed", "render failed")

    adapter = FailingCutMeAdapter()
    with pytest.raises(CutMeAdapterError, match="render failed"):
        run_final_video_pipeline(
            FakeWorkflow(),
            project_id=23,
            account_label="小博",
            product_card_template_id="muban-xiaobo-1",
            package_output_path=package_path,
            output_path=output_mp4,
                pipeline_path=pipeline_path,
                intro_video_path=intro_mp4,
                intro_video_text="测试引言",
            acceptance_mode="full",
            cutme_root=tmp_path,
            cutme_adapter=adapter,
            runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("post-processing must not run")
            ),
            probe_video=lambda path: (_ for _ in ()).throw(
                AssertionError("ffprobe must not run")
            ),
            measure_loudness=lambda path: (_ for _ in ()).throw(
                AssertionError("loudness verification must not run")
            ),
        )

    assert adapter.calls == 1
    assert not output_mp4.exists()
    assert pipeline_path.read_text(encoding="utf-8") == confirmed_pipeline
    assert not (workspace / "project-23" / "runs").exists()


def test_run_final_video_pipeline_puts_intro_in_package_without_outer_concat(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    intro_mp4 = tmp_path / "intro.mp4"
    full_mp4 = tmp_path / "full.mp4"
    package_path.write_text(
        json.dumps({"schemaVersion": "1.0.0", "segments": []}),
        encoding="utf-8",
    )
    intro_mp4.write_bytes(b"intro")

    prepared = {}

    class FakeWorkflow:
        def regenerate_product_card_images(self, *args, **kwargs):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, *args, **kwargs):
            prepared.update(kwargs)
            return {"ok": True, "package_path": str(package_path), "next": {}}

    class FakeCutMeAdapter:
        def __init__(self):
            self.calls = 0

        def render_final(self, package, *, output_path, cache_dir):
            self.calls += 1
            job_package_path.parent.mkdir(parents=True, exist_ok=True)
            job_package_path.write_text("{}", encoding="utf-8")
            product_mp4.write_bytes(b"product")
            return {
                "ok": True,
                "artifacts": {
                    "source_package_path": str(package_path),
                    "job_package_path": str(job_package_path),
                    "output_path": str(product_mp4),
                },
                "cache": None,
                "timings": {"total_ms": 10},
            }

    adapter = FakeCutMeAdapter()

    def fail_concat(command, *, cwd, timeout):
        raise AssertionError(f"unexpected command: {command}")

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=product_mp4,
        intro_video_path=intro_mp4,
        intro_video_text="引言字幕",
        full_output_path=full_mp4,
        acceptance_mode="none",
        cutme_root=tmp_path,
        cutme_adapter=adapter,
        runner=fail_concat,
        probe_video=lambda path: {"duration": 1.0},
    )

    assert adapter.calls == 1
    assert prepared["intro_video_path"] == str(intro_mp4)
    assert prepared["intro_video_text"] == "引言字幕"
    assert prepared["include_outro"] is True
    assert result["cutme"]["concat_intro"] is None
    assert product_mp4.is_file()
    assert not full_mp4.exists()


def test_run_final_video_pipeline_rejects_unknown_subtitle_alignment(tmp_path: Path):
    class FakeWorkflow:
        def regenerate_product_card_images(self, *args, **kwargs):
            raise AssertionError("should validate before mutating workflow state")

    try:
        run_final_video_pipeline(
            FakeWorkflow(),
            project_id=3,
            account_label="小博",
            subtitle_alignment="guess",
            cutme_root=tmp_path,
        )
    except ValueError as exc:
        assert "unsupported subtitle_alignment" in str(exc)
    else:
        raise AssertionError("expected unsupported subtitle_alignment to fail")


def test_run_final_video_pipeline_builds_renders_verifies_and_extracts_frames(tmp_path: Path):
    calls: list[object] = []
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    output_mp4 = tmp_path / "final.mp4"

    package = {
        "schemaVersion": "1.0.0",
        "segments": [
            {"type": "price_transition", "duration": 2.0},
            {"type": "product_recommendation", "duration": 4.0, "videoAsset": "assets/p001.mp4"},
            {"type": "product_recommendation", "duration": 4.0},
        ],
    }
    package_path.write_text(json.dumps(package), encoding="utf-8")

    class FakeWorkflow:
        def prepare_product_recommendation_output(
            self,
            project_id,
            *,
            account_label,
            output_mode,
            product_media_mode,
            product_order_strategy,
            mode,
            top_uids,
            product_card_template_id,
            package_output_path,
            subtitle_alignment,
            intro_video_path=None,
            intro_video_text="",
            include_outro=False,
            closing_text="",
            dynamic_product_contexts=None,
            master_snapshot_id=None,
            episode_id="",
        ):
            calls.append(
                (
                    "package",
                    project_id,
                    account_label,
                    output_mode,
                    product_media_mode,
                    product_order_strategy,
                    mode,
                    top_uids,
                    product_card_template_id,
                    package_output_path,
                    subtitle_alignment,
                    dynamic_product_contexts,
                    master_snapshot_id,
                    )
            )
            return {
                "ok": True,
                "package_path": str(package_path),
                "segment_counts": {"price_transition": 1, "product_recommendation": 2},
                "next": {"target_mp4": str(output_mp4)},
            }

    def fake_runner(command, *, cwd, timeout):
        calls.append(("run", command, str(cwd), timeout))
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    def fake_probe(path):
        calls.append(("probe", str(path)))
        return {"duration": 10.0, "video": "h264 1920x1080 30fps", "audio": "aac 48000Hz"}

    def fake_loudness(path):
        calls.append(("loudness", str(path)))
        return {"output_i": "-11.04", "output_tp": "-1.00"}

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=3,
        account_label="小燃",
        product_media_mode="video_preferred",
        product_order_strategy="stable",
        mode="standard",
        top_uids="",
        product_card_template_id="muban-xiaobo-1",
        package_output_path=str(package_path),
        output_path=str(output_mp4),
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=fake_probe,
        measure_loudness=fake_loudness,
    )

    assert result["ok"] is True
    assert result["package_path"] == str(package_path)
    assert result["job_package_path"] == str(job_package_path)
    assert result["output_mp4"] == str(output_mp4)
    assert result["output_mp4_link"] == f"[打开完整 MP4]({output_mp4.as_posix()})"
    assert [frame["label"] for frame in result["frames"]] == [
        "price-transition",
        "product-video",
        "later-product",
    ]
    assert calls[:1] == [
        (
            "package",
            3,
            "小燃",
            "final_mp4",
            "video_preferred",
            "stable",
            "standard",
            "",
            "muban-xiaobo-1",
            str(package_path),
            "asr",
            [],
            "test-snapshot",
        ),
    ]
    assert result["verification"]["ffprobe"]["duration"] == 10.0
    assert result["product_order_strategy"] == "stable"
    assert result["verification"]["loudnorm"]["output_i"] == "-11.04"
    assert result["price_transition_report"]["count"] == 1
    assert result["price_transition_report"]["items"][0]["after_top_products"] == 0
    manifest_path = Path(result["run_manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["kind"] == "bworkflow.final_video_run"
    assert manifest["asset_model"]["asset_library"] == "reusable_copy_and_parameter_assets"
    assert manifest["asset_model"]["pipeline"] == "this_run_selection"
    assert manifest["asset_model"]["run_manifest"] == "generation_evidence"
    assert manifest["project"]["id"] == 3
    assert manifest["selection"]["product_media_mode"] == "video_preferred"
    assert manifest["selection"]["product_order_strategy"] == "stable"
    assert manifest["outputs"]["product_mp4"] == str(output_mp4)
    assert manifest["outputs"]["full_mp4"] is None
    assert [item["position"] for item in manifest["segments"]["price_transitions"]] == [1]
    assert [item["position"] for item in manifest["segments"]["products"]] == [2, 3]
    package_fingerprint = next(
        item for item in manifest["file_fingerprints"] if item["role"] == "render_package"
    )
    assert package_fingerprint["exists"] is True
    assert len(package_fingerprint["sha256"]) == 64


def test_run_final_video_pipeline_renders_intro_and_outro_in_one_mp4_with_quick_acceptance(tmp_path: Path):
    calls: list[object] = []
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
    formal_delivery_dir = tmp_path / "formal-delivery"
    intro_mp4 = tmp_path / "intro-subtitle.mp4"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [
                    {"type": "product_recommendation", "productUid": "P001", "duration": 3.0},
                    {"type": "price_transition", "priceRangeLabel": "100-200元", "duration": 2.0},
                    {"type": "product_recommendation", "productUid": "P002", "duration": 3.0},
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {
                "ok": True,
                "package_path": str(package_path),
                "segment_counts": {"price_transition": 1, "product_recommendation": 2},
                "next": {"target_mp4": str(product_mp4)},
            }

    def fake_runner(command, *, cwd, timeout):
        calls.append(("run", command, str(cwd), timeout))
        if "-filter_complex" in command and str(full_mp4) in command:
            full_mp4.write_bytes(b"full")
            return {"stdout": "concat\n", "stderr": "", "returncode": 0}
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=product_mp4,
        intro_video_path=intro_mp4,
        intro_video_text="引言口播文字",
        full_output_path=full_mp4,
        delivery_dir=formal_delivery_dir,
        acceptance_mode="quick",
        mode="top",
        top_uids="P001",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 10.0, "audio": "aac 48000Hz", "path": str(path)},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert result["ok"] is True
    assert result["output_mp4"] == str(full_mp4)
    assert result["full_output_mp4"] == str(full_mp4)
    assert full_mp4.is_file()
    assert not list(formal_delivery_dir.glob("*.mp4"))
    assert result["full_output_mp4_link"] == f"[打开完整 MP4]({full_mp4.as_posix()})"
    assert result["acceptance_mode"] == "quick"
    assert result["verification"]["loudnorm"] is None
    assert result["verification"]["full_ffprobe"]["path"] == str(full_mp4)
    concat_commands = [item[1] for item in calls if item[0] == "run" and "-filter_complex" in item[1]]
    assert concat_commands == []
    assert result["price_transition_report"]["count"] == 1
    assert result["price_transition_report"]["items"][0]["position"] == 2
    assert result["price_transition_report"]["items"][0]["after_top_products"] == 1
    assert result["intro_subtitles"]["source"] == "render_package_asr"
    manifest_path = Path(result["run_manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["product_mp4"] == str(full_mp4)
    assert manifest["outputs"]["full_mp4"] == str(full_mp4)
    assert manifest["inputs"]["intro_video_path"] == str(intro_mp4.resolve())
    assert manifest["inputs"]["intro_subtitles"]["source"] == "render_package_asr"
    assert manifest["selection"]["mode"] == "top"
    assert manifest["selection"]["top_uids"] == ["P001"]
    assert manifest["reports"]["price_transition_report"]["items"][0]["after_top_products"] == 1


def test_run_final_video_pipeline_keeps_explicit_output_with_intro_and_delivery_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    explicit_output = tmp_path / "requested" / "custom-final.mp4"
    delivery_dir = tmp_path / "delivery"
    intro_mp4 = tmp_path / "intro.mp4"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [{"type": "product_recommendation", "productUid": "P001", "duration": 3.0}],
            }
        ),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {
                "ok": True,
                "package_path": str(package_path),
                "next": {"target_mp4": str(tmp_path / "ignored-default.mp4")},
            }

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=explicit_output,
        delivery_dir=delivery_dir,
        intro_video_path=intro_mp4,
        intro_video_text="引言口播文字",
        acceptance_mode="quick",
        cutme_root=tmp_path,
        probe_video=lambda path: {"duration": 3.0, "path": str(path)},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert explicit_output.is_file()
    assert result["output_mp4"] == str(explicit_output)
    assert result["full_output_mp4"] == str(explicit_output)
    assert not list(delivery_dir.glob("*.mp4"))
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["outputs"]["product_mp4"] == str(explicit_output)
    assert manifest["outputs"]["full_mp4"] == str(explicit_output)


def test_run_final_video_pipeline_keeps_only_final_mp4_in_delivery_dir(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    delivery_dir = tmp_path / "delivery"
    package_path = tmp_path / "source-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    intro_mp4 = tmp_path / "intro-subtitle.mp4"
    captured: dict[str, Path] = {}
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [
                    {"type": "price_transition", "duration": 1.0},
                    {"type": "product_recommendation", "duration": 2.0},
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            captured["package_output"] = Path(kwargs["package_output_path"])
            run_dir = captured["package_output"].parent
            timestamp = run_dir.parent.name
            captured["process_dir"] = run_dir
            captured["evidence_dir"] = run_dir.parent / "acceptance"
            captured["frames_dir"] = captured["evidence_dir"] / "frames"
            captured["product_mp4"] = run_dir / f"product-section-{timestamp}.mp4"
            captured["full_mp4"] = delivery_dir / f"完整成片-{timestamp}.mp4"
            assert captured["package_output"] == run_dir / "render-package.json"
            return {
                "ok": True,
                "package_path": str(package_path),
                "next": {"target_mp4": str(tmp_path / "old-default.mp4")},
            }

    def fake_runner(command, *, cwd, timeout):
        if "-filter_complex" in command and str(captured["full_mp4"]) in command:
            captured["full_mp4"].write_bytes(b"full")
            return {"stdout": "", "stderr": "", "returncode": 0}
        if "-frames:v" in command:
            frame_path = Path(command[-1])
            assert frame_path.parent == captured["frames_dir"]
            frame_path.write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        delivery_dir=delivery_dir,
        intro_video_path=intro_mp4,
        intro_video_text="引言口播文字",
        acceptance_mode="visual",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 3.0, "path": str(path)},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert result["output_mp4"] == str(captured["full_mp4"])
    assert result["full_output_mp4"] == str(captured["full_mp4"])
    assert result["package_path"] == str(package_path)
    assert result["delivery"]["dir"] == str(delivery_dir)
    assert result["delivery"]["evidence_dir"] == str(captured["evidence_dir"])
    assert result["delivery"]["process_dir"] == str(captured["process_dir"])
    assert all(Path(frame["path"]).parent == captured["frames_dir"] for frame in result["frames"])
    assert not captured["product_mp4"].exists()
    assert captured["full_mp4"].is_file()
    assert not (delivery_dir / "01_最终成片").exists()
    assert not (delivery_dir / "02_验收证据").exists()
    assert not (delivery_dir / "03_过程记录").exists()
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["outputs"]["product_mp4"] == str(captured["full_mp4"])
    assert manifest["outputs"]["full_mp4"] == str(captured["full_mp4"])
    assert manifest["delivery"]["dir"] == str(delivery_dir)
    assert manifest["delivery"]["evidence_dir"] == str(captured["evidence_dir"])


def test_run_final_video_pipeline_passes_asr_subtitle_alignment_to_package_builder(tmp_path: Path):
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    output_mp4 = tmp_path / "final.mp4"
    package_path.write_text(json.dumps({"schemaVersion": "1.0.0", "segments": []}), encoding="utf-8")
    captured: dict[str, str] = {}

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            captured["subtitle_alignment"] = kwargs["subtitle_alignment"]
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(output_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    run_final_video_pipeline(
        FakeWorkflow(),
        project_id=3,
        account_label="小博",
        package_output_path=package_path,
        output_path=output_mp4,
        subtitle_alignment="asr",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 1.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert captured["subtitle_alignment"] == "asr"


def test_run_final_video_pipeline_routes_intro_text_into_render_package(tmp_path: Path):
    calls: list[list[str]] = []
    prepared = {}
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
    intro_mp4 = tmp_path / "intro.mp4"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(json.dumps({"schemaVersion": "1.0.0", "segments": []}), encoding="utf-8")

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        calls.append(command)
        if "-filter_complex" in command and str(full_mp4) in command:
            full_mp4.write_bytes(b"full")
            return {"stdout": "", "stderr": "", "returncode": 0}
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=product_mp4,
        intro_video_path=intro_mp4,
        intro_video_text="这是引言字幕",
        full_output_path=full_mp4,
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 3.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert not (tmp_path / "intro-subtitles.ass").exists()
    assert not any("-filter_complex" in command for command in calls)


def test_run_final_video_pipeline_records_intro_source_plan_for_batch_asr(tmp_path: Path):
    calls: list[list[str]] = []
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
    intro_mp4 = tmp_path / "intro.mp4"
    source_plan = tmp_path / "source-intro-plan.json"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(json.dumps({"schemaVersion": "1.0.0", "segments": []}), encoding="utf-8")
    source_plan.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "type": "hook_open",
                        "text": "第一段按模板拆",
                        "timing": {"start": 0.4, "duration": 1.6},
                    },
                    {
                        "type": "pain_points",
                        "text": "第二段继续按模板",
                        "timing": {"start": 2.0, "duration": 2.0},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        calls.append(command)
        if "-filter_complex" in command and str(full_mp4) in command:
            full_mp4.write_bytes(b"full")
            return {"stdout": "", "stderr": "", "returncode": 0}
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=product_mp4,
        intro_video_path=intro_mp4,
        intro_video_text="这是一整段兜底文案不应该进入字幕文件",
        intro_video_source_plan_path=source_plan,
        full_output_path=full_mp4,
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 5.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert not (tmp_path / "intro-subtitles.ass").exists()
    assert result["intro_subtitle_source_plan_path"] == str(source_plan)
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["inputs"]["intro_subtitles"]["status"] == "ready"
    assert manifest["inputs"]["intro_subtitles"]["source"] == "render_package_asr"
    assert manifest["inputs"]["intro_subtitles"]["event_count"] is None


def test_run_final_video_pipeline_uses_source_plan_text_and_retimes_with_asr(tmp_path: Path):
    prepared = {}
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
    intro_mp4 = tmp_path / "intro-draft.mp4"
    source_plan = tmp_path / "source-intro-plan.json"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(json.dumps({"schemaVersion": "1.0.0", "segments": []}), encoding="utf-8")
    source_plan.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "type": "hook_open",
                        "text": "这段有文案但是没有 timing",
                        "timing": None,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            prepared.update(kwargs)
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        if "-filter_complex" in command and str(full_mp4) in command:
            raise AssertionError("should not concat an intro with zero subtitle events")
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=product_mp4,
        intro_video_path=intro_mp4,
        intro_video_source_plan_path=source_plan,
        full_output_path=full_mp4,
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 5.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert result["ok"] is True
    assert prepared["intro_video_text"] == "这段有文案但是没有 timing"
    assert prepared["subtitle_alignment"] == "asr"


def test_run_final_video_pipeline_blocks_intro_video_without_subtitle_source(tmp_path: Path):
    prepared = {}
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
    intro_mp4 = tmp_path / "intro-draft.mp4"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(json.dumps({"schemaVersion": "1.0.0", "segments": []}), encoding="utf-8")

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            prepared.update(kwargs)
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        if "-filter_complex" in command and str(full_mp4) in command:
            raise AssertionError("should not concat an intro without a subtitle source")
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    try:
        run_final_video_pipeline(
            FakeWorkflow(),
            project_id=23,
            account_label="小博",
            package_output_path=package_path,
            output_path=product_mp4,
            intro_video_path=intro_mp4,
            full_output_path=full_mp4,
            cutme_root=tmp_path,
            runner=fake_runner,
            probe_video=lambda path: {"duration": 5.0},
            measure_loudness=lambda path: {"output_i": "-11.0"},
        )
    except ValueError as exc:
        assert "intro subtitle blocked" in str(exc)
        assert "requires transcript text or a source plan" in str(exc)
    else:
        raise AssertionError("expected missing intro subtitle source to block final MP4 generation")


def test_run_final_video_pipeline_passes_absolute_paths_to_cutme(
    tmp_path: Path,
    monkeypatch,
    fake_cutme_boundary,
):
    monkeypatch.chdir(tmp_path)
    package_path = tmp_path / "relative-package.json"
    output_mp4 = tmp_path / "relative-final.mp4"
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [{"type": "price_transition", "duration": 1.0}],
            }
        ),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": "relative-package.json", "next": {"target_mp4": "relative-final.mp4"}}

    run_final_video_pipeline(
        FakeWorkflow(),
        project_id=3,
        account_label="小燃",
        package_output_path="relative-package.json",
        output_path="relative-final.mp4",
        acceptance_mode="none",
        cutme_root=tmp_path,
        probe_video=lambda path: {"duration": 1.0},
    )

    assert len(fake_cutme_boundary) == 1
    source, output, _cache = fake_cutme_boundary[0]
    assert source == package_path.resolve()
    assert output == output_mp4.resolve()


def test_run_final_video_pipeline_passes_project_level_cache_dir_to_cutme(
    tmp_path: Path,
    monkeypatch,
    fake_cutme_boundary,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    output_mp4 = tmp_path / "exports" / "final.mp4"
    cache_dir = tmp_path / "workspace" / "render-cache" / "clip-cache-v1"
    clip_cache_manifest = cache_dir / "clip-cache-manifest.json"
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [{"type": "price_transition", "duration": 1.0}],
            }
        ),
        encoding="utf-8",
    )

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(output_mp4)}}

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="灏忓崥",
        package_output_path=package_path,
        output_path=output_mp4,
        acceptance_mode="none",
        cutme_root=tmp_path,
        probe_video=lambda path: {"duration": 1.0},
    )

    assert len(fake_cutme_boundary) == 1
    assert fake_cutme_boundary[0][2] == cache_dir
    assert result["cutme"]["clip_cache_dir"] == str(cache_dir)
    assert result["cutme"]["clip_cache_manifest"] == str(clip_cache_manifest)
    assert result["cutme"]["clip_cache"]["cache_hits"] == 1
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    clip_cache_fingerprint = next(
        item for item in manifest["file_fingerprints"] if item["role"] == "clip_cache_manifest"
    )
    assert clip_cache_fingerprint["exists"] is True


def test_read_clip_cache_summary_preserves_performance_and_mastering_evidence(tmp_path: Path):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    manifest = tmp_path / "clip-cache-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "summary": {"segments_total": 24, "cache_hits": 24, "rendered": 0},
                "timings": {"concat_ms": 105000, "loudnorm_ms": 31000},
                "videoEncoding": {"final": {"encoder": "libx264"}},
                "mastering": {"changed": False, "reason": "already_compliant"},
            }
        ),
        encoding="utf-8",
    )

    result = pipeline_module._read_clip_cache_summary(manifest)

    assert result["timings"]["concat_ms"] == 105000
    assert result["video_encoding"]["final"]["encoder"] == "libx264"
    assert result["mastering"] == {"changed": False, "reason": "already_compliant"}


def test_run_final_video_pipeline_records_latest_run_in_pipeline(tmp_path: Path, monkeypatch):
    prepared = {}
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
    intro_mp4 = tmp_path / "intro-subtitle.mp4"
    pipeline_path = tmp_path / ".pipeline.json"
    intro_mp4.write_bytes(b"intro")
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [{"type": "product_recommendation", "productUid": "P001", "duration": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    pipeline_path.write_text(
        json.dumps(
            {
                "current_phase": "intro_video",
                "phases": {
                    "assembly": {
                        "run_manifest_path": "old.run-manifest.json",
                        "final_mp4_path": "old.mp4",
                    }
                },
                "paths": {"manifest": "old.run-manifest.json", "final_mp4": "old.mp4"},
            }
        ),
        encoding="utf-8",
    )
    confirm_phase7_selection(
        pipeline_path,
        output_branch="final_mp4",
        account="小博",
        product_card_template_id="muban-xiaobo-1",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        mode="top",
        top_uids="P001",
    )
    confirm_intro_video(pipeline_path, intro_mp4, approved_at="2026-07-19T00:00:00Z")

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            prepared.update(kwargs)
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        if "-filter_complex" in command and str(full_mp4) in command:
            full_mp4.write_bytes(b"full")
            return {"stdout": "", "stderr": "", "returncode": 0}
        if "-frames:v" in command:
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        product_media_mode="video_preferred",
        product_order_strategy="price_segment_shuffle",
        product_card_template_id="muban-xiaobo-1",
        mode="top",
        top_uids="P001",
        package_output_path=package_path,
        output_path=product_mp4,
        intro_video_path=intro_mp4,
        intro_video_text="引言口播文字",
        full_output_path=full_mp4,
        pipeline_path=pipeline_path,
        acceptance_mode="quick",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 1.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    saved = json.loads(pipeline_path.read_text(encoding="utf-8"))
    assert saved["current_phase"] == "assembly"
    assert saved["phases"]["assembly"]["status"] == "done"
    assert saved["phases"]["assembly"]["run_manifest_path"] == result["run_manifest_path"]
    assert saved["phases"]["assembly"]["final_mp4_path"] == str(full_mp4)
    assert "product_only_mp4_path" not in saved["phases"]["assembly"]
    assert saved["phases"]["assembly"]["product_card_template_id"] == "muban-xiaobo-1"
    assert saved["phases"]["assembly"]["mode"] == "top"
    assert saved["phases"]["assembly"]["top_uids"] == ["P001"]
    assert saved["paths"]["manifest"] == result["run_manifest_path"]
    assert saved["paths"]["final_mp4"] == str(full_mp4)


def test_run_final_video_pipeline_quick_acceptance_skips_visual_frames_and_records_timings(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    output_mp4 = tmp_path / "final.mp4"
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [{"type": "price_transition", "duration": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(output_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        commands.append(command)
        if "-frames:v" in command:
            raise AssertionError("quick acceptance should not extract visual frames")
        raise AssertionError(f"unexpected command: {command}")

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=output_mp4,
        acceptance_mode="quick",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 1.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert result["frames"] == []
    assert result["verification"]["loudnorm"] is None
    assert "timings" in result
    assert result["timings"]["total_ms"] >= 0
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["reports"]["timings"]["total_ms"] >= 0
    assert all("-frames:v" not in command for command in commands)


def test_run_final_video_pipeline_visual_acceptance_extracts_frames_without_loudnorm(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    output_mp4 = tmp_path / "final.mp4"
    package_path.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "segments": [
                    {"type": "price_transition", "duration": 1.0},
                    {"type": "product_recommendation", "duration": 1.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    frame_commands = 0
    loudnorm_calls = 0

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(output_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        nonlocal frame_commands
        if "-frames:v" in command:
            frame_commands += 1
            Path(command[-1]).write_bytes(b"png")
            return {"stdout": "", "stderr": "", "returncode": 0}
        raise AssertionError(f"unexpected command: {command}")

    def fake_loudness(path):
        nonlocal loudnorm_calls
        loudnorm_calls += 1
        return {"output_i": "-11.0"}

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="小博",
        package_output_path=package_path,
        output_path=output_mp4,
        acceptance_mode="visual",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 1.0},
        measure_loudness=fake_loudness,
    )

    assert frame_commands == len(result["frames"])
    assert frame_commands > 0
    assert loudnorm_calls == 0


def test_run_command_decodes_windows_local_encoded_chinese_paths(tmp_path: Path):
    completed = _run_command(
        [
            "python",
            "-c",
            "import sys; sys.stdout.buffer.write('G:/workspace/赵二-工具-CutMe'.encode('gbk'))",
        ],
        cwd=tmp_path,
        timeout=30,
    )

    assert completed.stdout == "G:/workspace/赵二-工具-CutMe"
