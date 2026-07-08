from __future__ import annotations

import json
from pathlib import Path

from bworkflow_sql.final_video_pipeline import _run_command, run_final_video_pipeline


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
        ),
    ]
    assert result["verification"]["ffprobe"]["duration"] == 10.0
    assert result["product_order_strategy"] == "stable"
    assert result["verification"]["loudnorm"]["output_i"] == "-11.04"
    assert result["price_transition_report"]["count"] == 1
    assert result["price_transition_report"]["items"][0]["after_top_products"] == 0


def test_run_final_video_pipeline_concats_intro_video_into_full_mp4_with_quick_acceptance(tmp_path: Path):
    calls: list[object] = []
    package_path = tmp_path / "render-package.json"
    job_package_path = tmp_path / "job" / "render-package.json"
    product_mp4 = tmp_path / "product.mp4"
    full_mp4 = tmp_path / "full.mp4"
    intro_mp4 = tmp_path / "intro.mp4"
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
