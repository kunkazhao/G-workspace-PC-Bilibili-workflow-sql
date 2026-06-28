from __future__ import annotations

import json
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
            "common_folder_name": "通用",
            "clip_slots": [
                {"role": "product_demo", "source": "category_folder"},
                {"role": "triple_cta", "source": "common_folder", "match_keywords": ["引导三连"]},
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
    common_dir = asset_root / "通用"
    category_dir.mkdir(parents=True)
    common_dir.mkdir(parents=True)
    for index in range(1, 5):
        (category_dir / f"product-{index}.mp4").write_bytes(b"")
    (common_dir / "引导三连1.mp4").write_bytes(b"")
    (common_dir / "点赞1.mp4").write_bytes(b"")

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
    assert prepared["preflight"]["ok"] is True
    assert prepared["pc_workflow"]["aligned_with_asr"] is False
    assert json.loads(output_plan.read_text(encoding="utf-8"))["selected_assets"] == selected


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
