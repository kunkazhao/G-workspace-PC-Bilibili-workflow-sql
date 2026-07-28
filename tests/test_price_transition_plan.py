from __future__ import annotations

import json
from pathlib import Path

import pytest

from bworkflow_sql.db import Database
from bworkflow_sql.price_transition_plan import (
    find_price_transition_plan_for_text,
    price_transition_card_from_plan,
    validate_price_transition_plan_set,
    write_price_transition_plan_for_project,
)
from bworkflow_sql.repositories import Repository


def water_flosser_plan() -> dict:
    return {
        "transitions": [
            {
                "price_range_label": "100元以下",
                "block_label": "正文",
                "transition_text": "一百元以下先看水流稳定和档位调节，日常清洁够用，适合第一次买冲牙器的人。",
                "audience": "适合第一次买冲牙器的人",
                "items": [
                    {"label": "水流稳定", "trigger_text": "水流稳定"},
                    {"label": "档位调节", "trigger_text": "档位调节"},
                ],
            },
            {
                "price_range_label": "100-200元",
                "block_label": "正文",
                "transition_text": "一百到两百元重点看水箱容量、喷嘴适配和清洗便利，适合正畸或者多人使用。",
                "audience": "适合正畸或者多人使用",
                "items": [
                    {"label": "水箱容量", "trigger_text": "水箱容量"},
                    {"label": "喷嘴适配", "trigger_text": "喷嘴适配"},
                    {"label": "清洗便利", "trigger_text": "清洗便利"},
                ],
            },
        ]
    }


def test_validate_price_transition_plan_rejects_trigger_missing_from_copy():
    plan = water_flosser_plan()
    plan["transitions"][0]["items"][0]["trigger_text"] = "脉冲频率"

    with pytest.raises(ValueError, match="触发词必须按顺序原样出现在"):
        validate_price_transition_plan_set(plan)


def test_validate_price_transition_plan_accepts_multiple_versions_for_one_range():
    plan = water_flosser_plan()
    duplicate = dict(plan["transitions"][0])
    duplicate["block_label"] = "正文二"
    plan["transitions"].append(duplicate)

    result = validate_price_transition_plan_set(plan)

    assert [
        (item["price_range_label"], item["block_label"])
        for item in result["transitions"]
    ] == [("100元以下", "正文"), ("100-200元", "正文"), ("100元以下", "正文二")]


def test_validate_price_transition_plan_rejects_duplicate_range_and_label():
    plan = water_flosser_plan()
    plan["transitions"].append(dict(plan["transitions"][0]))

    with pytest.raises(ValueError, match="价格过渡计划存在重复版本"):
        validate_price_transition_plan_set(plan)


def test_writer_updates_formal_markdown_without_sync(tmp_path: Path, monkeypatch):
    import bworkflow_sql.price_transition_plan as plan_module

    monkeypatch.setattr(plan_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    markdown = tmp_path / "冲牙器.md"
    markdown.write_text("## 商品文案\n\n## 价格过渡文案\n", encoding="utf-8")
    project_id = db.upsert_project({"name": "家居-冲牙器", "md_path": str(markdown)})
    source = tmp_path / "price-plan.json"
    plan = water_flosser_plan()
    variant = json.loads(json.dumps(plan["transitions"][0], ensure_ascii=False))
    variant["block_label"] = "正文二"
    variant["transition_text"] = "一百元以下另一种讲法也看水流稳定和档位调节，适合第一次买冲牙器的人。"
    plan["transitions"].append(variant)
    source.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = write_price_transition_plan_for_project(
        db=db,
        project_id=project_id,
        plan_input_path=source,
        markdown_path=markdown,
        sync=False,
    )

    text = markdown.read_text(encoding="utf-8")
    assert "### 100元以下" in text
    assert "#### 正文" in text
    assert "#### 正文二" in text
    assert "水流稳定和档位调节" in text
    assert result.transition_count == 3
    assert result.plan_path.is_file()
    assert repo.script_blocks(project_id) == []
    matched = find_price_transition_plan_for_text(
        project_id,
        price_range_label="100元以下",
        block_label="正文",
        body=plan_module.load_price_transition_plan_set(project_id)["transitions"][0]["transition_text"],
    )
    assert matched is not None
    assert [item["label"] for item in matched["items"]] == ["水流稳定", "档位调节"]


def test_plan_card_uses_explicit_water_flosser_labels():
    transition = validate_price_transition_plan_set(water_flosser_plan())["transitions"][1]

    card = price_transition_card_from_plan(transition, duration=8.0)

    assert card["planVersion"] == "1.0.0"
    assert [item["label"] for item in card["items"]] == ["水箱容量", "喷嘴适配", "清洗便利"]
    assert [item["triggerText"] for item in card["items"]] == ["水箱容量", "喷嘴适配", "清洗便利"]
    assert card["audience"] == "适合正畸或者多人使用"
