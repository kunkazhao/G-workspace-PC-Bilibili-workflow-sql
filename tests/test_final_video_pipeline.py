from __future__ import annotations

import json
from pathlib import Path

import pytest

from bworkflow_sql.final_video_pipeline import _run_command, run_final_video_pipeline


pytestmark = pytest.mark.usefixtures("isolated_final_video_workspace")


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
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            calls.append(("images", project_id, account_label, mode, product_uid, product_card_template_id))
            return {"ok": True, "regenerated": [{"uid": "P001"}], "skipped": []}

        def prepare_product_recommendation_output(
            self,
            project_id,
            *,
            account_label,
            output_mode,
            product_media_mode,
            product_order_strategy,
            stale_product_image_policy,
            mode,
            top_uids,
            product_card_template_id,
            package_output_path,
            subtitle_alignment,
        ):
            calls.append(
                (
                    "package",
                    project_id,
                    account_label,
                    output_mode,
                    product_media_mode,
                    product_order_strategy,
                    stale_product_image_policy,
                    mode,
                    top_uids,
                    product_card_template_id,
                    package_output_path,
                    subtitle_alignment,
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
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            output_mp4.write_bytes(b"mp4")
            return {"stdout": "rendered\n", "stderr": "", "returncode": 0}
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
        product_image_mode="missing",
        stale_product_image_policy="block",
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
    assert calls[:2] == [
        ("images", 3, "小燃", "missing", "", "muban-xiaobo-1"),
        (
            "package",
            3,
            "小燃",
            "final_mp4",
            "video_preferred",
            "stable",
            "block",
            "standard",
            "",
            "muban-xiaobo-1",
            str(package_path),
            "proportional",
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


def test_run_final_video_pipeline_concats_intro_video_into_full_mp4_with_quick_acceptance(tmp_path: Path):
    calls: list[object] = []
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
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
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            product_mp4.write_bytes(b"product")
            return {"stdout": "rendered\n", "stderr": "", "returncode": 0}
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
        full_output_path=full_mp4,
        acceptance_mode="quick",
        mode="top",
        top_uids="P001",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 10.0, "audio": "aac 48000Hz", "path": str(path)},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert result["ok"] is True
    assert result["output_mp4"] == str(product_mp4)
    assert result["full_output_mp4"] == str(full_mp4)
    assert result["full_output_mp4_link"] == f"[打开完整 MP4]({full_mp4.as_posix()})"
    assert result["acceptance_mode"] == "quick"
    assert result["verification"]["loudnorm"] is None
    assert result["verification"]["full_ffprobe"]["path"] == str(full_mp4)
    concat_commands = [item[1] for item in calls if item[0] == "run" and "-filter_complex" in item[1]]
    assert len(concat_commands) == 1
    concat_text = " ".join(concat_commands[0])
    assert "loudnorm=I=-11:TP=-1:LRA=11,aresample=48000" in concat_text
    assert "-ar 48000" in concat_text
    assert result["price_transition_report"]["count"] == 1
    assert result["price_transition_report"]["items"][0]["position"] == 2
    assert result["price_transition_report"]["items"][0]["after_top_products"] == 1
    assert result["intro_subtitles"]["source"] == "embedded_intro_mp4"
    manifest_path = Path(result["run_manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["outputs"]["product_mp4"] == str(product_mp4)
    assert manifest["outputs"]["full_mp4"] == str(full_mp4)
    assert manifest["inputs"]["intro_video_path"] == str(intro_mp4.resolve())
    assert manifest["inputs"]["intro_subtitles"]["source"] == "embedded_intro_mp4"
    assert manifest["selection"]["mode"] == "top"
    assert manifest["selection"]["top_uids"] == ["P001"]
    assert manifest["reports"]["price_transition_report"]["items"][0]["after_top_products"] == 1


def test_run_final_video_pipeline_delivery_dir_keeps_mp4s_at_root_and_evidence_in_subdirs(
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
            timestamp = run_dir.name
            captured["process_dir"] = run_dir
            captured["evidence_dir"] = delivery_dir / "02_验收证据" / timestamp
            captured["frames_dir"] = captured["evidence_dir"] / "frames"
            captured["product_mp4"] = delivery_dir / f"商品推荐段-{timestamp}.mp4"
            captured["full_mp4"] = delivery_dir / f"完整成片-{timestamp}.mp4"
            assert captured["package_output"] == run_dir / "render-package.json"
            return {
                "ok": True,
                "package_path": str(package_path),
                "next": {"target_mp4": str(tmp_path / "old-default.mp4")},
            }

    def fake_runner(command, *, cwd, timeout):
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            assert command[command.index("--output") + 1] == str(captured["product_mp4"])
            captured["product_mp4"].write_bytes(b"product")
            return {"stdout": "", "stderr": "", "returncode": 0}
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
        acceptance_mode="visual",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 3.0, "path": str(path)},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert result["output_mp4"] == str(captured["product_mp4"])
    assert result["full_output_mp4"] == str(captured["full_mp4"])
    assert result["package_path"] == str(package_path)
    assert result["delivery"]["dir"] == str(delivery_dir)
    assert result["delivery"]["evidence_dir"] == str(captured["evidence_dir"])
    assert result["delivery"]["process_dir"] == str(captured["process_dir"])
    assert all(Path(frame["path"]).parent == captured["frames_dir"] for frame in result["frames"])
    assert captured["product_mp4"].is_file()
    assert captured["full_mp4"].is_file()
    assert not (delivery_dir / "01_最终成片").exists()
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["outputs"]["product_mp4"] == str(captured["product_mp4"])
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
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            output_mp4.write_bytes(b"mp4")
            return {"stdout": "", "stderr": "", "returncode": 0}
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


def test_run_final_video_pipeline_burns_intro_subtitles_before_concat(tmp_path: Path):
    calls: list[list[str]] = []
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
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            product_mp4.write_bytes(b"product")
            return {"stdout": "", "stderr": "", "returncode": 0}
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

    concat_command = next(command for command in calls if "-filter_complex" in command and str(full_mp4) in command)
    filter_complex = concat_command[concat_command.index("-filter_complex") + 1]
    assert "subtitles=" in filter_complex
    assert "intro-subtitles" in filter_complex
    assert (tmp_path / "intro-subtitles.ass").is_file()


def test_run_final_video_pipeline_uses_intro_source_plan_for_subtitle_splitting(tmp_path: Path):
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
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            product_mp4.write_bytes(b"product")
            return {"stdout": "", "stderr": "", "returncode": 0}
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

    ass_text = (tmp_path / "intro-subtitles.ass").read_text(encoding="utf-8")
    assert "第一段按模板拆" in ass_text
    assert "第二段继续按模板" in ass_text
    assert "兜底文案" not in ass_text
    assert "Dialogue: 0,0:00:00.40,0:00:02.00" in ass_text
    assert result["intro_subtitle_source_plan_path"] == str(source_plan)
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["inputs"]["intro_subtitles"]["status"] == "ready"
    assert manifest["inputs"]["intro_subtitles"]["source"] == "source_plan"
    assert manifest["inputs"]["intro_subtitles"]["event_count"] == 2


def test_run_final_video_pipeline_blocks_intro_source_plan_without_timing_or_fallback_text(tmp_path: Path):
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
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            product_mp4.write_bytes(b"product")
            return {"stdout": "", "stderr": "", "returncode": 0}
        if "-filter_complex" in command and str(full_mp4) in command:
            raise AssertionError("should not concat an intro with zero subtitle events")
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
            intro_video_source_plan_path=source_plan,
            full_output_path=full_mp4,
            cutme_root=tmp_path,
            runner=fake_runner,
            probe_video=lambda path: {"duration": 5.0},
            measure_loudness=lambda path: {"output_i": "-11.0"},
        )
    except ValueError as exc:
        assert "intro subtitle" in str(exc)
        assert "source plan" in str(exc)
    else:
        raise AssertionError("expected zero intro subtitle events to block final MP4 generation")


def test_run_final_video_pipeline_blocks_intro_video_without_subtitle_source(tmp_path: Path):
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
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            product_mp4.write_bytes(b"product")
            return {"stdout": "", "stderr": "", "returncode": 0}
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
        assert "neither source plan nor fallback text" in str(exc)
    else:
        raise AssertionError("expected missing intro subtitle source to block final MP4 generation")


def test_run_final_video_pipeline_passes_absolute_paths_to_cutme(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    package_path = tmp_path / "relative-package.json"
    output_mp4 = tmp_path / "relative-final.mp4"
    job_package_path = tmp_path / "job" / "render-package.json"
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
            return {"ok": True, "package_path": "relative-package.json", "next": {"target_mp4": "relative-final.mp4"}}

    def fake_runner(command, *, cwd, timeout):
        commands.append(command)
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            output_mp4.write_bytes(b"mp4")
            return {"stdout": "", "stderr": "", "returncode": 0}
        Path(command[-1]).write_bytes(b"png")
        return {"stdout": "", "stderr": "", "returncode": 0}

    run_final_video_pipeline(
        FakeWorkflow(),
        project_id=3,
        account_label="小燃",
        package_output_path="relative-package.json",
        output_path="relative-final.mp4",
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 1.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    assert commands[0] == [
        "python",
        "-m",
        "cutme",
        "--package",
        str(package_path.resolve()),
        "--build-render-job",
    ]
    assert str(output_mp4.resolve()) in commands[1]


def test_run_final_video_pipeline_passes_project_level_cache_dir_to_cutme(
    tmp_path: Path,
    monkeypatch,
):
    import bworkflow_sql.final_video_pipeline as pipeline_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    package_path = tmp_path / "render-package.json"
    output_mp4 = tmp_path / "exports" / "final.mp4"
    job_package_path = tmp_path / "job" / "render-package.json"
    cache_dir = tmp_path / "workspace" / "project-23" / "render" / "final-video-cache"
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
    commands: list[list[str]] = []

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(output_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        commands.append(command)
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            output_mp4.parent.mkdir(parents=True, exist_ok=True)
            output_mp4.write_bytes(b"mp4")
            clip_cache_manifest.parent.mkdir(parents=True, exist_ok=True)
            clip_cache_manifest.write_text(
                json.dumps(
                    {
                        "summary": {
                            "segments_total": 1,
                            "cache_hits": 1,
                            "rendered": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            return {"stdout": "", "stderr": "", "returncode": 0}
        Path(command[-1]).write_bytes(b"png")
        return {"stdout": "", "stderr": "", "returncode": 0}

    result = run_final_video_pipeline(
        FakeWorkflow(),
        project_id=23,
        account_label="灏忓崥",
        package_output_path=package_path,
        output_path=output_mp4,
        cutme_root=tmp_path,
        runner=fake_runner,
        probe_video=lambda path: {"duration": 1.0},
        measure_loudness=lambda path: {"output_i": "-11.0"},
    )

    render_command = commands[1]
    assert "--cache-dir" in render_command
    assert render_command[render_command.index("--cache-dir") + 1] == str(cache_dir)
    assert result["cutme"]["clip_cache_dir"] == str(cache_dir)
    assert result["cutme"]["clip_cache_manifest"] == str(clip_cache_manifest)
    assert result["cutme"]["clip_cache"]["cache_hits"] == 1
    manifest = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
    clip_cache_fingerprint = next(
        item for item in manifest["file_fingerprints"] if item["role"] == "clip_cache_manifest"
    )
    assert clip_cache_fingerprint["exists"] is True


def test_run_final_video_pipeline_records_latest_run_in_pipeline(tmp_path: Path, monkeypatch):
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

    class FakeWorkflow:
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid, product_card_template_id):
            return {"ok": True, "regenerated": [], "skipped": []}

        def prepare_product_recommendation_output(self, project_id, **kwargs):
            return {"ok": True, "package_path": str(package_path), "next": {"target_mp4": str(product_mp4)}}

    def fake_runner(command, *, cwd, timeout):
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            product_mp4.write_bytes(b"product")
            return {"stdout": "", "stderr": "", "returncode": 0}
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
        product_media_mode="cover_only",
        product_order_strategy="price_segment_shuffle",
        product_card_template_id="muban-xiaobo-3",
        mode="top",
        top_uids="P001",
        package_output_path=package_path,
        output_path=product_mp4,
        intro_video_path=intro_mp4,
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
    assert saved["phases"]["assembly"]["product_only_mp4_path"] == str(product_mp4)
    assert saved["phases"]["assembly"]["product_card_template_id"] == "muban-xiaobo-3"
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
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            output_mp4.write_bytes(b"mp4")
            return {"stdout": "", "stderr": "", "returncode": 0}
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
        if command[-1] == "--build-render-job":
            return {"stdout": f"RenderPackage: {job_package_path}\n", "stderr": "", "returncode": 0}
        if "--render-fast-final" in command:
            output_mp4.write_bytes(b"mp4")
            return {"stdout": "", "stderr": "", "returncode": 0}
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
