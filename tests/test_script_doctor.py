from __future__ import annotations

import json
from pathlib import Path

import pytest

from bworkflow_sql.db import Database
from bworkflow_sql.repositories import Repository
from bworkflow_sql.script_doctor import diagnose_script_flow
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.episode_materializer import materialize_episode_markdown


def _seed_project(tmp_path: Path) -> tuple[Database, int, Path]:
    db = Database(tmp_path / "script-doctor.db")
    repo = Repository(db)
    md_path = tmp_path / "episode.md"
    project_id = db.upsert_project(
        {
            "name": "数码-键盘",
            "scheme_id": "scheme-1",
            "scheme_name": "主方案",
            "md_path": str(md_path),
        }
    )
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "P001", "title": "Alpha Keyboard", "price_label": "299元"},
            {"uid": "P002", "title": "Beta Keyboard", "price_label": "399元"},
        ],
    )
    return db, project_id, md_path


def _seed_project_with_category(tmp_path: Path) -> tuple[Database, int, Path]:
    db = Database(tmp_path / "script-doctor.db")
    repo = Repository(db)
    md_path = tmp_path / "spoken" / "episode.md"
    project_id = db.upsert_project(
        {
            "name": "数码-键盘",
            "category_parent_name": "数码",
            "category_name": "键盘",
            "scheme_id": "scheme-1",
            "scheme_name": "主方案",
            "md_path": str(md_path),
        }
    )
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "P001", "title": "Alpha Keyboard", "price_label": "299元"},
            {"uid": "P002", "title": "Beta Keyboard", "price_label": "399元"},
        ],
    )
    return db, project_id, md_path


def _write_matching_intro_plan(tmp_path: Path, project_id: int, intro_text: str) -> None:
    workspace = tmp_path / "workspace" / f"project-{project_id}" / "intro"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "source-intro-plan-引言1.json").write_text(
        json.dumps(
            {
                "template_id": "pain_avoidance_priority_v1",
                "full_script": intro_text,
                "visual_events": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_matching_price_plan(tmp_path: Path, project_id: int, price_text: str) -> None:
    import bworkflow_sql.price_transition_plan as price_plan_module

    plan = price_plan_module.validate_price_transition_plan_set(
        {
            "transitions": [
                {
                    "price_range_label": "300-500元",
                    "block_label": "正文",
                    "transition_text": price_text,
                    "audience": "适合看连接和手感稳定性",
                    "items": [
                        {"label": "连接", "trigger_text": "连接"},
                        {"label": "手感稳定", "trigger_text": "手感稳定性"},
                    ],
                }
            ]
        }
    )
    workspace = tmp_path / "workspace" / f"project-{project_id}" / "price-transitions"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "source-price-transition-plan-set.json").write_text(
        json.dumps(plan, ensure_ascii=False),
        encoding="utf-8",
    )


def test_script_doctor_reports_missing_content_units(tmp_path: Path, monkeypatch):
    import bworkflow_sql.markdown_paths as markdown_paths_module

    db, project_id, md_path = _seed_project(tmp_path)
    empty_library_root = tmp_path / "empty-copy-library"
    monkeypatch.setattr(markdown_paths_module, "DEFAULT_MARKDOWN_ROOT", empty_library_root)
    md_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 的单品文案。
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id)
    issue_codes = {issue["code"] for issue in result["issues"]}

    assert result["ok"] is False
    assert result["status"] == "content_incomplete"
    assert result["summary"]["products_total"] == 2
    assert result["summary"]["product_copy_ready"] == 1
    assert "missing_product_copy" in issue_codes
    assert "missing_intro_content" in issue_codes
    assert result["summary"]["price_transition_sections"] == 0
    assert result["summary"]["price_transition_ready"] == 0
    assert result["next"]["action"] == "fill_content_units"
    assert result["next"]["task"] == "写文案草稿"
    assert result["next"]["command"] == f"python -m bworkflow_sql research-pack {project_id}"
    assert result["next"]["outline_command"] == f"python -m bworkflow_sql outline {project_id}"
    assert result["next"]["research_pack_path"].endswith("数码-键盘\\主方案.md")
    assert result["next"]["requires_user_final_approval"] is True


def test_script_doctor_reports_empty_price_transition_body(tmp_path: Path):
    db, project_id, md_path = _seed_project(tmp_path)
    intro_text = "先看清预算，再看桌面空间。"
    md_path.write_text(
        f"""
## 引言文案

### 引言1

{intro_text}

## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 的单品文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 的单品文案。

## 价格过渡文案

### 300-500元

#### 正文1
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id, intro_label="引言1")

    assert result["summary"]["price_transition_sections"] == 1
    assert result["summary"]["price_transition_ready"] == 0
    assert any(issue["code"] == "missing_price_transition_copy" for issue in result["issues"])


def test_script_doctor_blocks_product_copy_lint_failures(tmp_path: Path):
    db, project_id, md_path = _seed_project(tmp_path)
    md_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha Keyboard，预算三百元左右可以优先看。

### 399元-P002-Beta Keyboard

#### 正文

Beta Keyboard，是这期三百到五百档的主推，页面标注支持三模连接。
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id)
    lint_issues = [issue for issue in result["issues"] if issue["code"] == "product_copy_lint_failed"]

    assert result["ok"] is False
    assert result["status"] == "content_incomplete"
    assert result["summary"]["product_copy_ready"] == 1
    assert result["summary"]["product_copy_lint_failed_products"] == 1
    assert result["summary"]["product_copy_lint_findings"] == 3
    assert {issue["rule_id"] for issue in lint_issues} == {
        "internal_role_label",
        "internal_price_tier",
        "source_page_reference",
    }
    assert all(issue["uid"] == "P002" for issue in lint_issues)
    assert result["next"]["action"] == "fix_product_copy_language"
    assert result["next"]["command"] == f"python -m bworkflow_sql copy-lint {project_id}"


def test_script_doctor_reports_product_copy_style_without_blocking_status_rule(tmp_path: Path):
    from bworkflow_sql.script_doctor import _status

    db, project_id, md_path = _seed_project(tmp_path)
    md_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha Keyboard 有独立方向键。需要频繁改表格，这件更对路。

### 399元-P002-Beta Keyboard

#### 正文

Beta Keyboard 支持三模连接。桌面设备多，这件很实在。
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id)
    style_issues = [issue for issue in result["issues"] if issue["code"] == "product_copy_style_warning"]

    assert result["summary"]["product_copy_style_findings"] == 2
    assert {issue["rule_id"] for issue in style_issues} == {"voice_phrase_rejected"}
    assert all(issue["blocking"] is False for issue in style_issues)
    assert _status(style_issues, True) == "ready_for_downstream"


def test_script_doctor_reports_reusable_library_copy_when_episode_is_missing(tmp_path: Path, monkeypatch):
    import bworkflow_sql.markdown_paths as markdown_paths_module

    db, project_id, _md_path = _seed_project(tmp_path)
    library_root = tmp_path / "copy-library"
    library_path = library_root / "数码-键盘.md"
    library_path.parent.mkdir(parents=True)
    library_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 已经写好的复用文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 已经写好的复用文案。
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(markdown_paths_module, "DEFAULT_MARKDOWN_ROOT", library_root)

    result = diagnose_script_flow(db, project_id=project_id)

    assert result["ok"] is False
    assert result["status"] == "content_incomplete"
    assert result["summary"]["product_copy_ready"] == 0
    assert result["summary"]["product_copy_library_ready"] == 2
    assert result["next"]["action"] == "materialize_episode_markdown"
    assert result["next"]["command"] == f"python -m bworkflow_sql materialize-episode {project_id}"
    assert result["next"]["source_path"] == str(library_path)
    assert not any(issue["code"] == "missing_product_copy" for issue in result["issues"])
    assert any(issue["code"] == "episode_markdown_needs_materialization" for issue in result["issues"])


def test_script_doctor_reports_reusable_library_price_transitions_when_episode_is_missing(tmp_path: Path, monkeypatch):
    import bworkflow_sql.markdown_paths as markdown_paths_module

    db, project_id, _md_path = _seed_project(tmp_path)
    library_root = tmp_path / "copy-library"
    library_path = library_root / "数码-键盘.md"
    library_path.parent.mkdir(parents=True)
    library_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 已经写好的复用文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 已经写好的复用文案。

## 价格过渡文案

### 300元以下

#### 正文

三百以内先看基础体验。

### 300-500元

#### 正文

三百到五百开始看做工和连接。
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(markdown_paths_module, "DEFAULT_MARKDOWN_ROOT", library_root)

    result = diagnose_script_flow(db, project_id=project_id)

    assert result["summary"]["price_transition_sections"] == 0
    assert result["summary"]["price_transition_library_ready"] == 2
    assert not any(issue["code"] == "missing_price_transition_copy" for issue in result["issues"])
    assert any(issue["code"] == "episode_price_transition_needs_materialization" for issue in result["issues"])


def test_script_doctor_uses_asset_markdown_when_project_md_path_points_to_spoken_artifact(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module
    import bworkflow_sql.markdown_paths as markdown_paths_module

    monkeypatch.setattr(markdown_paths_module, "DEFAULT_MARKDOWN_ROOT", tmp_path / "copy-library")
    monkeypatch.setattr(markdown_paths_module, "DEFAULT_SPOKEN_MD_ROOT", tmp_path / "spoken")
    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")

    db, project_id, spoken_path = _seed_project_with_category(tmp_path)
    spoken_path.parent.mkdir(parents=True)
    spoken_path.write_text(
        """
## 引言文案

### 引言1

这是一次性口播稿，不应该作为资产源。
""".strip(),
        encoding="utf-8",
    )
    asset_path = tmp_path / "copy-library" / "数码-键盘.md"
    asset_path.parent.mkdir(parents=True)
    intro_text = "最近想买键盘吗？这是资产库里的标准引言。"
    _write_matching_intro_plan(tmp_path, project_id, intro_text)
    asset_path.write_text(
        f"""
## 引言文案

### 引言1

{intro_text}

## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 资产文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 资产文案。

## 价格过渡文案

### 300-500元

#### 正文

三百到五百看轴体和连接。
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id, intro_label="引言1")

    assert result["project"]["md_path"] == str(asset_path)
    assert result["project"]["bound_md_path"] == str(spoken_path)
    assert result["summary"]["product_copy_ready"] == 2
    assert result["summary"]["price_transition_ready"] == 1
    assert result["selected_intro"]["source_intro_plan_path"].endswith("source-intro-plan-引言1.json")
    assert any(issue["code"] == "project_md_path_points_to_spoken_artifact" for issue in result["issues"])


def test_materialize_episode_markdown_copies_reusable_product_copy(tmp_path: Path):
    db, project_id, md_path = _seed_project(tmp_path)
    library_path = tmp_path / "library.md"
    library_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 复用文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 复用文案。
""".strip(),
        encoding="utf-8",
    )

    result = materialize_episode_markdown(db, project_id=project_id, library_path=library_path)

    text = md_path.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["materialized"] == 2
    assert result["missing_library_copy"] == []
    assert "Alpha 复用文案。" in text
    assert "Beta 复用文案。" in text
    assert "## 引言文案" in text
    assert "## 商品文案" in text
    assert "## 价格过渡文案" in text
    assert db.fetchall("SELECT * FROM script_blocks WHERE project_id=?", (project_id,)) == []


def test_materialize_episode_markdown_rejects_final_spoken_output_path(tmp_path: Path):
    db, project_id, md_path = _seed_project(tmp_path)
    spoken_path = tmp_path / "final-spoken.md"
    spoken_path.write_text("FINAL SPOKEN SENTINEL\n", encoding="utf-8")
    db.execute(
        "UPDATE projects SET md_path=?, spoken_md_path=? WHERE id=?",
        (str(spoken_path), str(spoken_path), project_id),
    )
    library_path = tmp_path / "library.md"
    library_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 复用文案。
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="final spoken Markdown"):
        materialize_episode_markdown(
            db,
            project_id=project_id,
            library_path=library_path,
        )

    assert spoken_path.read_text(encoding="utf-8") == "FINAL SPOKEN SENTINEL\n"
    assert not md_path.exists()


def test_materialize_episode_markdown_copies_reusable_price_transitions(tmp_path: Path):
    db, project_id, md_path = _seed_project(tmp_path)
    library_path = tmp_path / "library.md"
    library_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 复用文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 复用文案。

## 价格过渡文案

### 300元以下

#### 正文

三百以内先看能不能把电脑外放替掉，别先追求大动态。

### 300-500元

#### 正文

三百到五百开始看连接、声场和桌面摆放，适合想认真升级电脑声音的人。
""".strip(),
        encoding="utf-8",
    )

    result = materialize_episode_markdown(db, project_id=project_id, library_path=library_path)

    text = md_path.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["price_transitions_materialized"] == 2
    assert "## 价格过渡文案" in text
    assert "### 300元以下" in text
    assert "三百以内先看能不能把电脑外放替掉" in text
    assert "### 300-500元" in text
    assert "三百到五百开始看连接、声场和桌面摆放" in text


def test_materialize_episode_markdown_refreshes_asset_intro_from_source_plan(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module
    import bworkflow_sql.markdown_paths as markdown_paths_module

    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr(markdown_paths_module, "DEFAULT_MARKDOWN_ROOT", tmp_path / "copy-library")
    monkeypatch.setattr(markdown_paths_module, "DEFAULT_SPOKEN_MD_ROOT", tmp_path / "spoken")
    db, project_id, spoken_path = _seed_project_with_category(tmp_path)
    spoken_path.parent.mkdir(parents=True)
    spoken_path.write_text(
        """
## 引言文案

### 引言1

一次性口播稿不能被改成资产源。
""".strip(),
        encoding="utf-8",
    )
    asset_path = tmp_path / "copy-library" / "数码-键盘.md"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_text(
        """
## 引言文案

旧的散落正文，不应该继续当成一个引言版本。

### 引言1

旧的引言1。
""".strip(),
        encoding="utf-8",
    )
    intro_text = "最近想买桌面音响吗？这是标准引言模板1生成的文案。"
    _write_matching_intro_plan(tmp_path, project_id, intro_text)
    asset_path.write_text(
        """
## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 复用文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 复用文案。
""".strip(),
        encoding="utf-8",
    )

    result = materialize_episode_markdown(db, project_id=project_id)

    text = asset_path.read_text(encoding="utf-8")
    spoken_text = spoken_path.read_text(encoding="utf-8")
    assert result["ok"] is True
    assert result["target_path"] == str(asset_path)
    assert "### 引言1" in text
    assert intro_text in text
    assert "旧的引言1" not in text
    assert "旧的散落正文" not in text
    assert "一次性口播稿不能被改成资产源" in spoken_text


def test_script_doctor_reports_ready_to_sync_when_copy_units_exist(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module
    import bworkflow_sql.price_transition_plan as price_plan_module

    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr(price_plan_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db, project_id, md_path = _seed_project(tmp_path)
    intro_text = "最近想买键盘吗？先别急着看参数。"
    price_text = "这个价位开始更适合看连接和手感稳定性。"
    _write_matching_intro_plan(tmp_path, project_id, intro_text)
    _write_matching_price_plan(tmp_path, project_id, price_text)
    md_path.write_text(
        f"""
## 引言文案

### 引言1

{intro_text}

## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 的单品文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 的单品文案。

## 价格过渡文案

### 300-500元

#### 正文

{price_text}
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id, intro_label="引言1")

    assert result["ok"] is False
    assert result["status"] == "needs_sync"
    assert result["summary"]["intro_ready"] == 1
    assert result["summary"]["product_copy_ready"] == 2
    assert result["summary"]["price_transition_ready"] == 1
    assert result["selected_intro"]["label"] == "引言1"
    assert result["selected_intro"]["source_intro_plan_path"].endswith("source-intro-plan-引言1.json")
    assert result["next"]["action"] == "sync_markdown"
    assert result["next"]["command"] == f"python -m bworkflow_sql sync {project_id} --step markdown"
    assert result["next"]["task"] == "定稿后同步入库"
    assert result["next"]["requires_user_final_approval"] is True


def test_script_doctor_prioritizes_missing_intro_plan_next_hint(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module

    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db, project_id, md_path = _seed_project(tmp_path)
    intro_text = "最近想买键盘吗？先别急着看参数。"
    md_path.write_text(
        f"""
## 引言文案

### 引言1

{intro_text}

## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 的单品文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 的单品文案。

## 价格过渡文案

### 300-500元

#### 正文

这个价位开始更适合看连接和手感稳定性。
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id, intro_label="引言1")

    assert result["status"] == "content_incomplete"
    assert any(issue["code"] == "missing_matching_intro_plan" for issue in result["issues"])
    assert result["next"]["action"] == "create_intro_plan"
    assert result["next"]["task"] == "补引言剪辑计划"
    assert result["next"]["command"] == f"python -m bworkflow_sql intro-plan {project_id} --slots <slots.json> --label 引言1"
    assert result["next"]["requires_user_final_approval"] is False


def test_script_doctor_blocks_price_transition_plan_hash_mismatch(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module
    import bworkflow_sql.price_transition_plan as price_plan_module

    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr(price_plan_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db, project_id, md_path = _seed_project(tmp_path)
    intro_text = "最近想买键盘吗？先看真实使用场景。"
    price_text = "三百到五百元重点看连接稳定和按键手感，适合长期办公的人。"
    md_path.write_text(
        f"""
## 引言文案

### 引言1

{intro_text}

## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 的单品文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 的单品文案。

## 价格过渡文案

### 300-500元

#### 正文

{price_text}后来又手动改了一句。
""".strip(),
        encoding="utf-8",
    )
    _write_matching_intro_plan(tmp_path, project_id, intro_text)
    plan = price_plan_module.validate_price_transition_plan_set(
        {
            "transitions": [
                {
                    "price_range_label": "300-500元",
                    "block_label": "正文",
                    "transition_text": price_text,
                    "audience": "适合长期办公的人",
                    "items": [
                        {"label": "连接稳定", "trigger_text": "连接稳定"},
                        {"label": "按键手感", "trigger_text": "按键手感"},
                    ],
                }
            ]
        }
    )
    plan_path = price_plan_module.price_transition_plan_path(project_id)
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = diagnose_script_flow(db, project_id=project_id, intro_label="引言1")

    assert result["status"] == "content_incomplete"
    assert any(issue["code"] == "missing_matching_price_transition_plan" for issue in result["issues"])
    assert result["next"]["action"] == "rebuild_price_transition_plan"
    assert result["next"]["requires_user_final_approval"] is False


def test_script_doctor_reports_ready_after_markdown_sync(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module
    import bworkflow_sql.price_transition_plan as price_plan_module

    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    monkeypatch.setattr(price_plan_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db, project_id, md_path = _seed_project(tmp_path)
    intro_text = "最近想买键盘吗？先别急着看参数。"
    price_text = "这个价位开始更适合看连接和手感稳定性。"
    _write_matching_intro_plan(tmp_path, project_id, intro_text)
    _write_matching_price_plan(tmp_path, project_id, price_text)
    md_path.write_text(
        f"""
## 引言文案

### 引言1

{intro_text}

## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 的单品文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 的单品文案。

### 499元-OLD001-Old Keyboard

#### 正文

历史方案保留的正文。

## 价格过渡文案

### 300-500元

#### 正文

{price_text}
""".strip(),
        encoding="utf-8",
    )
    SyncService(db).sync_markdown(project_id)

    result = diagnose_script_flow(db, project_id=project_id, intro_label="引言1")

    assert result["ok"] is True
    assert result["status"] == "ready_for_downstream"
    assert result["summary"]["script_blocks_synced"] == 4
    assert result["next"]["action"] == "continue_downstream"
    assert result["next"]["task"] == "进入配音检查"
    assert any(issue["code"] == "extra_markdown_product" for issue in result["issues"])


def test_script_doctor_requires_selected_intro_when_multiple_versions(tmp_path: Path, monkeypatch):
    import bworkflow_sql.cutme_intro as cutme_intro_module

    monkeypatch.setattr(cutme_intro_module, "INTERNAL_WORKSPACE_ROOT", tmp_path / "workspace")
    db, project_id, md_path = _seed_project(tmp_path)
    md_path.write_text(
        """
## 引言文案

### 引言1

第一版引言。

### 引言2

第二版引言。

## 商品文案

### 299元-P001-Alpha Keyboard

#### 正文

Alpha 的单品文案。

### 399元-P002-Beta Keyboard

#### 正文

Beta 的单品文案。
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_script_flow(db, project_id=project_id)

    assert result["ok"] is False
    assert result["status"] == "content_incomplete"
    assert any(issue["code"] == "intro_version_not_selected" for issue in result["issues"])
    assert result["next"]["action"] == "select_intro_version"
