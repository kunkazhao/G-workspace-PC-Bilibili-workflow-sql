from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import bworkflow_sql.cutme_intro as cutme_intro_module


def _write_plan(path: Path) -> None:
    scenes = [
        {
            "type": "hook_open",
            "text": "A",
            "timing": {"start": 0.0, "duration": 1.0},
            "visual_cues": [{"clip_role": "product_demo"}],
        },
        {
            "type": "pain_points",
            "text": "B",
            "timing": {"start": 1.0, "duration": 1.0},
            "visual_cues": [{"clip_role": "product_demo"}],
        },
        {
            "type": "self_check",
            "text": "C",
            "timing": {"start": 2.0, "duration": 1.0},
            "visual_cues": [{"clip_role": "product_demo"}],
        },
        {
            "type": "priority_preview",
            "text": "D",
            "timing": {"start": 3.0, "duration": 1.0},
            "visual_cues": [{"clip_role": "triple_cta"}],
        },
    ]
    plan = {
        "full_script": "ABCD",
        "asset_contract": {
            "common_folder_name": "1-通用",
            "clip_slots": [
                {"role": "product_demo", "source": "category_folder"},
                {"role": "triple_cta", "source": "common_folder", "match_keywords": ["引导三连"]},
            ],
        },
        "sfx_contract": {
            "enabled_in_contract": True,
            "folder_name": "1-音效",
            "selection_policy": "exact_filename",
            "sfx_slots": [
                {"role": "text_pop", "filename": "sfx_text_pop.wav", "required": False},
                {"role": "text_swipe", "filename": "sfx_text_swipe.wav", "required": False},
                {"role": "title_hit", "filename": "sfx_title_hit.wav", "required": False},
                {"role": "transition_whoosh", "filename": "sfx_transition_whoosh.wav", "required": False},
                {"role": "progress_tick", "filename": "sfx_progress_tick.wav", "required": False},
                {"role": "cta_pop", "filename": "sfx_cta_pop.wav", "required": False},
            ],
        },
        "scenes": scenes,
    }
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def test_prepare_intro_plan_selects_assets_without_reuse(tmp_path: Path):
    source_plan = tmp_path / "intro_plan.json"
    output_plan = tmp_path / "prepared.json"
    _write_plan(source_plan)

    asset_root = tmp_path / "素材"
    category_dir = asset_root / "数码-键盘"
    common_dir = asset_root / "1-通用"
    sfx_dir = asset_root / "1-音效"
    category_dir.mkdir(parents=True)
    common_dir.mkdir(parents=True)
    sfx_dir.mkdir(parents=True)
    for index in range(1, 5):
        (category_dir / f"product-{index}.mp4").write_bytes(b"")
    (common_dir / "引导三连1.mp4").write_bytes(b"")
    (common_dir / "点赞1.mp4").write_bytes(b"")
    for filename in [
        "sfx_text_pop.wav",
        "sfx_text_swipe.wav",
        "sfx_title_hit.wav",
        "sfx_transition_whoosh.wav",
        "sfx_progress_tick.wav",
        "sfx_cta_pop.wav",
    ]:
        (sfx_dir / filename).write_bytes(b"")

    prepared = cutme_intro_module.prepare_intro_plan_for_cutme(
        source_plan_path=source_plan,
        audio_path=tmp_path / "intro.wav",
        project={"id": 1, "name": "数码-键盘"},
        account_label="小博",
        expected_intro_text="ABCD",
        output_plan_path=output_plan,
        asset_root=asset_root,
        seed="fixed",
    )

    selected = prepared["selected_assets"]
    assert len(selected["product_demo"]) == 3
    assert len(set(selected["product_demo"])) == 3
    assert selected["triple_cta"].endswith("引导三连1.mp4")
    assert set(selected["sfx"]) == {
        "text_pop",
        "text_swipe",
        "title_hit",
        "transition_whoosh",
        "progress_tick",
        "cta_pop",
    }
    assert selected["sfx"]["text_pop"].endswith("sfx_text_pop.wav")
    assert prepared["preflight"]["ok"] is True
    assert prepared["pc_workflow"]["aligned_with_asr"] is False
    assert prepared["pc_workflow"]["seed"] == "fixed"
    assert json.loads(output_plan.read_text(encoding="utf-8"))["selected_assets"] == selected
    report = json.loads(output_plan.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["renderer"] == "hyperframes"
    assert report["selected_assets"] == selected
    assert report["category_material_folder"] == str(category_dir)
    assert report["prepared_intro_plan_path"] == str(output_plan)
    checklist = report["acceptance_checklist"]
    assert checklist["must_report_to_user"] is True
    assert checklist["requires_user_approval_before_phase_7"] is True
    assert checklist["items"] == [
        "核对引言模板为 pain_avoidance_priority_v1",
        "核对 product_demo 素材均来自标准品类素材池",
        "核对 triple_cta 素材来自通用素材池",
        "抽帧检查片头画面和产品展示不跑偏",
        "用户确认 OK 后再进入阶段 7",
    ]


def test_intro_preflight_blocks_missing_product_demo_clips(tmp_path: Path):
    source_plan = tmp_path / "intro_plan.json"
    _write_plan(source_plan)
    asset_root = tmp_path / "assets"
    (asset_root / "数码-桌面音响").mkdir(parents=True)
    (asset_root / "1-通用").mkdir(parents=True)
    (asset_root / "1-通用" / "引导三连1.mp4").write_bytes(b"")

    result = cutme_intro_module.preflight_intro_plan_for_cutme(
        source_plan_path=source_plan,
        project={"id": 23, "name": "数码-桌面音响"},
        asset_root=asset_root,
    )

    assert result["ok"] is False
    assert result["status"] == "blocked_missing_intro_demo"
    assert result["requirements"]["product_demo"]["required"] == 3
    assert result["requirements"]["product_demo"]["available"] == 0
    assert "缺 3 段数码-桌面音响通用产品展示素材" in result["message"]
    assert result["next"]["action"] == "add_intro_product_demo_clips"


def test_intro_preflight_records_blocked_pipeline_state(tmp_path: Path):
    source_plan = tmp_path / "intro_plan.json"
    pipeline_path = tmp_path / ".pipeline.json"
    _write_plan(source_plan)
    asset_root = tmp_path / "assets"
    (asset_root / "数码-桌面音响").mkdir(parents=True)
    (asset_root / "1-通用").mkdir(parents=True)
    (asset_root / "1-通用" / "引导三连1.mp4").write_bytes(b"")

    result = cutme_intro_module.preflight_intro_plan_for_cutme(
        source_plan_path=source_plan,
        project={"id": 23, "name": "数码-桌面音响"},
        asset_root=asset_root,
        pipeline_path=pipeline_path,
    )

    saved = json.loads(pipeline_path.read_text(encoding="utf-8"))
    assert result["status"] == "blocked_missing_intro_demo"
    assert saved["current_phase"] == "intro_video"
    assert saved["last_error"]["code"] == "blocked_missing_intro_demo"
    assert saved["last_error"]["message"] == result["message"]
    assert saved["resume_hint"]["action"] == "add_intro_product_demo_clips"
    assert saved["phases"]["intro_video"]["status"] == "blocked"
    assert saved["phases"]["intro_video"]["preflight_status"] == "blocked_missing_intro_demo"
    assert saved["phases"]["intro_video"]["source_intro_plan_path"] == str(source_plan)
    assert saved["phases"]["intro_video"]["updated_at_utc"].endswith("Z")
    assert saved["phases"]["intro_video"]["updated_at_local"].endswith("+08:00")
    assert saved["phases"]["intro_video"]["updated_at"] == saved["phases"]["intro_video"]["updated_at_utc"]


def test_intro_preflight_rejects_recovered_intro_template(tmp_path: Path):
    source_plan = tmp_path / "intro_plan.json"
    _write_plan(source_plan)
    plan = json.loads(source_plan.read_text(encoding="utf-8"))
    plan["template_id"] = "recovered_markdown_intro_v1"
    source_plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = cutme_intro_module.preflight_intro_plan_for_cutme(
        source_plan_path=source_plan,
        project={"id": 23, "name": "数码-桌面音响"},
        asset_root=tmp_path / "assets",
    )

    assert result["ok"] is False
    assert result["status"] == "blocked_wrong_intro_template"
    assert "recovered_markdown_intro_v1" in result["message"]


def test_intro_preflight_rejects_selected_product_demo_outside_material_pool(tmp_path: Path):
    source_plan = tmp_path / "intro_plan.json"
    _write_plan(source_plan)
    asset_root = tmp_path / "assets"
    category_dir = asset_root / "数码-桌面音响"
    common_dir = asset_root / "1-通用"
    category_dir.mkdir(parents=True)
    common_dir.mkdir(parents=True)
    for index in range(1, 4):
        (category_dir / f"product-{index}.mp4").write_bytes(b"")
    (common_dir / "引导三连1.mp4").write_bytes(b"")

    outside = tmp_path / "single-review.mp4"
    outside.write_bytes(b"")
    plan = json.loads(source_plan.read_text(encoding="utf-8"))
    plan["selected_assets"] = {
        "product_demo": [
            str(category_dir / "product-1.mp4"),
            str(category_dir / "product-2.mp4"),
            str(outside),
        ],
        "triple_cta": str(common_dir / "引导三连1.mp4"),
    }
    source_plan.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = cutme_intro_module.preflight_intro_plan_for_cutme(
        source_plan_path=source_plan,
        project={"id": 23, "name": "数码-桌面音响"},
        asset_root=asset_root,
    )

    assert result["ok"] is False
    assert result["status"] == "blocked_invalid_intro_demo_source"
    assert str(outside) in result["issues"][0]["path"]


def test_intro_preflight_uses_intro_material_manifest_when_present(tmp_path: Path):
    source_plan = tmp_path / "intro_plan.json"
    _write_plan(source_plan)
    asset_root = tmp_path / "assets"
    category_dir = asset_root / "数码-桌面音响"
    common_dir = asset_root / "1-通用"
    category_dir.mkdir(parents=True)
    common_dir.mkdir(parents=True)
    for filename in ["approved-1.mp4", "approved-2.mp4", "unregistered.mp4"]:
        (category_dir / filename).write_bytes(b"")
    (common_dir / "引导三连1.mp4").write_bytes(b"")
    (category_dir / "intro-materials.json").write_text(
        json.dumps(
            {
                "materials": [
                    {"file": "approved-1.mp4", "role": "product_demo", "status": "approved"},
                    {"file": "approved-2.mp4", "role": "product_demo", "approved": True},
                    {"file": "unregistered.mp4", "role": "product_demo", "status": "draft"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = cutme_intro_module.preflight_intro_plan_for_cutme(
        source_plan_path=source_plan,
        project={"id": 23, "name": "数码-桌面音响"},
        asset_root=asset_root,
    )

    assert result["ok"] is False
    assert result["status"] == "blocked_missing_intro_demo"
    assert result["requirements"]["product_demo"]["available"] == 2
    assert all("unregistered.mp4" not in path for path in result["requirements"]["product_demo"]["files"])
    assert result["material_manifest_path"] == str(category_dir / "intro-materials.json")


def test_prepare_intro_plan_rejects_script_mismatch(tmp_path: Path):
    source_plan = tmp_path / "intro_plan.json"
    _write_plan(source_plan)

    with pytest.raises(ValueError, match="full_script"):
        cutme_intro_module.prepare_intro_plan_for_cutme(
            source_plan_path=source_plan,
            audio_path=tmp_path / "intro.wav",
            project={"id": 1, "name": "数码-键盘"},
            account_label="小博",
            expected_intro_text="changed",
            output_plan_path=tmp_path / "prepared.json",
            asset_root=tmp_path,
        )


def test_prepare_cutme_config_writes_intro_plan_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cutme_intro_module, "get_cutme_audio_duration", lambda _path: 12.5)
    config_path = tmp_path / "cutme-config.json"
    plan_path = tmp_path / "prepared.json"

    config = cutme_intro_module.prepare_cutme_config(
        config_path=config_path,
        intro_plan_path=plan_path,
        audio_path=tmp_path / "intro.wav",
        intro_text="ABCD",
        title="键盘怎么选？",
        asset_folder="",
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved == config
    assert saved["intro_plan_path"] == str(plan_path)
    assert saved["audio_duration"] == 12.5
    assert saved["seed"] == ""


def test_prepare_cutme_config_writes_intro_subtitle_contract_from_scenes(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(cutme_intro_module, "get_cutme_audio_duration", lambda _path: 12.5)
    config_path = tmp_path / "cutme-config.json"
    plan_path = tmp_path / "prepared.json"
    plan_path.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "type": "hook_open",
                        "text": "第一句先说清楚问题",
                        "timing": {"start": 0.0, "duration": 2.2},
                    },
                    {
                        "type": "pain_points",
                        "text": "第二句再讲避坑",
                        "timing": {"start": 2.2, "duration": 2.0},
                    },
                    {
                        "type": "empty",
                        "text": "",
                        "timing": {"start": 4.2, "duration": 1.0},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    config = cutme_intro_module.prepare_cutme_config(
        config_path=config_path,
        intro_plan_path=plan_path,
        audio_path=tmp_path / "intro.wav",
        intro_text="第一句先说清楚问题第二句再讲避坑",
        title="桌面音响怎么选？",
        asset_folder="",
    )

    subtitles = config["output"]["subtitles"]
    assert subtitles == {
        "enabled": True,
        "styleId": "impact_yellow",
        "source": "intro_plan_scenes",
        "scope": "standalone_intro",
    }
    assert config["subtitles"] == [
        {"start": 0.0, "end": 2.2, "text": "第一句先说清楚问题"},
        {"start": 2.2, "end": 4.2, "text": "第二句再讲避坑"},
    ]
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["subtitles"] == config["subtitles"]
    assert saved["output"]["subtitles"]["scope"] == "standalone_intro"


def test_prepare_cutme_config_splits_long_intro_scene_subtitles_with_shared_rules(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(cutme_intro_module, "get_cutme_audio_duration", lambda _path: 12.5)
    config_path = tmp_path / "cutme-config.json"
    plan_path = tmp_path / "prepared.json"
    long_scene_text = "如果你经常打游戏看电影，那沉浸感和声音规模，就不能太糊，比如人声和低频都要撑得住"
    plan_path.write_text(
        json.dumps(
            {
                "scenes": [
                    {
                        "type": "usage_scenarios",
                        "text": long_scene_text,
                        "timing": {"start": 8.0, "duration": 6.0},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    config = cutme_intro_module.prepare_cutme_config(
        config_path=config_path,
        intro_plan_path=plan_path,
        audio_path=tmp_path / "intro.wav",
        intro_text=long_scene_text,
        title="桌面音响怎么选？",
        asset_folder="",
    )

    subtitles = config["subtitles"]
    assert len(subtitles) > 1
    assert all(len(item["text"]) <= 24 for item in subtitles)
    assert "".join(item["text"] for item in subtitles) == "如果你经常打游戏看电影那沉浸感和声音规模就不能太糊比如人声和低频都要撑得住"
    assert subtitles[0]["start"] == 8.0
    assert subtitles[-1]["end"] == 14.0
    assert all(item["end"] > item["start"] for item in subtitles)


def test_run_cutme_render_passes_absolute_output_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "cutme-config.json"
    config_path.write_text("{}", encoding="utf-8")
    captured: dict[str, str] = {}

    def fake_run(command, **kwargs):
        captured["output"] = command[command.index("--output") + 1]
        output = Path(captured["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(cutme_intro_module.subprocess, "run", fake_run)

    result = cutme_intro_module.run_cutme_render(config_path, Path("relative") / "intro.mp4")

    assert Path(captured["output"]).is_absolute()
    assert result == tmp_path / "relative" / "intro.mp4"


def test_build_intro_visual_seed_is_fresh_per_prepare(monkeypatch):
    project = {"id": 1, "name": "数码-键盘"}
    tokens = iter(["aaa111", "bbb222"])
    monkeypatch.setattr(cutme_intro_module, "now_iso", lambda: "2026-06-30T06:00:00")
    monkeypatch.setattr(cutme_intro_module.secrets, "token_hex", lambda _size: next(tokens))

    first = cutme_intro_module.build_intro_visual_seed(
        project=project,
        account_label="小博",
        script_block_id=7,
    )
    second = cutme_intro_module.build_intro_visual_seed(
        project=project,
        account_label="小博",
        script_block_id=7,
    )
    assert first == "intro-2026-06-30T06:00:00-aaa111"
    assert second == "intro-2026-06-30T06:00:00-bbb222"
    assert first != second
    assert "小博" not in first
    assert "数码-键盘" not in first
