from __future__ import annotations

import json
from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.intro_plan_writer import (
    render_intro_plan_from_slots,
    write_intro_plan_for_project,
)
from bworkflow_sql.repositories import Repository


def keyboard_slots() -> dict[str, str]:
    return {
        "category": "键盘",
        "common_mistake_1": "轴体",
        "common_mistake_2": "RGB",
        "common_mistake_3": "热插拔",
        "pain_1": "声音太吵",
        "pain_2": "手感太累",
        "pain_3": "配列不适合桌面",
        "scene_1": "办公打字",
        "criteria_1": "舒服和安静",
        "flashy_selling_point": "花哨功能",
        "scene_2": "打游戏",
        "criteria_2": "响应稳定性",
        "criteria_3": "按键反馈",
        "scene_3": "桌面空间不大",
        "criteria_4": "配列和无线连接",
        "bad_result": "鼠标都没地方放",
        "standard_1": "轴体手感",
        "standard_2": "声音控制",
        "standard_3": "连接方式",
    }


def test_render_intro_plan_from_slots_keeps_template_contract():
    plan = render_intro_plan_from_slots(slots=keyboard_slots())

    assert plan["template_id"] == "pain_avoidance_priority_v1"
    assert plan["full_script"].startswith("最近想买键盘吗？")
    assert "你可以先想一下，自己主要的使用场景，和更看重的是什么" in plan["full_script"]
    assert [scene["type"] for scene in plan["scenes"]] == [
        "hook_open",
        "pain_points",
        "usage_scenarios",
        "method",
        "self_check",
        "priority_preview",
        "handoff_cta",
    ]
    cue_roles = [
        cue["clip_role"]
        for scene in plan["scenes"]
        for cue in scene.get("visual_cues", [])
    ]
    assert cue_roles == ["product_demo", "product_demo", "product_demo", "triple_cta"]


def test_write_intro_plan_for_project_writes_markdown_and_syncs(tmp_path: Path, monkeypatch):
    import bworkflow_sql.intro_plan_writer as writer_module

    monkeypatch.setattr(writer_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    md_path = tmp_path / "copy.md"
    md_path.write_text(
        """
## 引言文案

### 引言1

## 商品文案
""".strip(),
        encoding="utf-8",
    )
    project_id = db.upsert_project({"name": "数码-键盘", "md_path": str(md_path)})
    slots_path = tmp_path / "slots.json"
    slots_path.write_text(json.dumps(keyboard_slots(), ensure_ascii=False), encoding="utf-8")

    result = write_intro_plan_for_project(
        db=db,
        project_id=project_id,
        slots_path=slots_path,
        label="引言1",
        markdown_path=md_path,
        sync=True,
    )

    assert result.intro_plan_path.is_file()
    assert result.slots_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert "### 引言1" in text
    assert result.full_script in text
    blocks = repo.script_blocks(project_id)
    intro = next(block for block in blocks if block["script_type"] == "intro")
    assert intro["block_label"] == "引言1"
    assert intro["body"] == result.full_script
    assert intro["script_id"].startswith("intro:")


def test_cutme_intro_finds_matching_source_plan(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module

    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    workspace = cutme_intro_module.default_intro_plan_workspace(12)
    workspace.mkdir(parents=True)
    plan_path = workspace / "source-intro-plan.json"
    plan_path.write_text(
        json.dumps({"full_script": "最近想买键盘吗？听好了。"}, ensure_ascii=False),
        encoding="utf-8",
    )

    matched = cutme_intro_module.find_intro_plan_for_text(12, "最近想买键盘吗？\n听好了。")

    assert matched == plan_path
