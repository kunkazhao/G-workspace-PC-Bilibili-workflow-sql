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
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid):
            calls.append(("images", project_id, account_label, mode, product_uid))
            return {"ok": True, "regenerated": [{"uid": "P001"}], "skipped": []}

        def prepare_product_recommendation_output(
            self,
            project_id,
            *,
            account_label,
            output_mode,
            product_media_mode,
            stale_product_image_policy,
            mode,
            top_uids,
            package_output_path,
        ):
            calls.append(
                (
                    "package",
                    project_id,
                    account_label,
                    output_mode,
                    product_media_mode,
                    stale_product_image_policy,
                    mode,
                    top_uids,
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
        product_image_mode="missing",
        stale_product_image_policy="block",
        mode="standard",
        top_uids="",
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
        ("images", 3, "小燃", "missing", ""),
        (
            "package",
            3,
            "小燃",
            "final_mp4",
            "video_preferred",
            "block",
            "standard",
            "",
            str(package_path),
        ),
    ]
    assert result["verification"]["ffprobe"]["duration"] == 10.0
    assert result["verification"]["loudnorm"]["output_i"] == "-11.04"


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
        def regenerate_product_card_images(self, project_id, *, account_label, mode, product_uid):
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
