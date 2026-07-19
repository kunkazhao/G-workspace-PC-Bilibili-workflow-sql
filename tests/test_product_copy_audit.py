from __future__ import annotations

from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.product_copy_audit import audit_parsed_product_copy, diagnose_product_copy_audit


def _document(*bodies: str) -> str:
    sections = ["## 商品文案"]
    for index, body in enumerate(bodies, start=1):
        sections.extend(
            [
                "",
                f"### {100 + index}元-P{index:03d}-商品{index}",
                "",
                "#### 正文",
                "",
                body,
            ]
        )
    return "\n".join(sections)


def test_audit_reports_rejected_voice_phrase_with_product_location():
    source = _document("海上公路采用原纱工艺。海上公路这一件很值得优先看。")

    findings = audit_parsed_product_copy(parse_markdown_text(source), source_text=source)

    finding = next(item for item in findings if item["rule_id"] == "voice_phrase_rejected")
    assert finding["uid"] == "P001"
    assert finding["block_label"] == "正文"
    assert finding["match"] == "值得优先看"
    assert finding["line"] == 7


def test_audit_allows_concrete_effect_or_boundary_at_the_end():
    source = _document(
        "面料轻薄，背部有透气结构。走路出汗后，热气能更快从衣服里排出去。",
        "帽檐和指洞补齐了覆盖。只在早晚短途通勤，不必为更高防护参数加预算。",
        "版型给肩背留了余量。里面叠一件短袖，抬手时也不会卡住肩膀。",
    )

    assert audit_parsed_product_copy(parse_markdown_text(source), source_text=source) == []


def test_audit_reports_document_level_repeated_abstract_closing_form():
    source = _document(
        "采用原纱工艺。经常正午活动，这件更对路。",
        "背部做了透气结构。想少闷汗，这件更实用。",
        "版型偏日常。平时通勤，这件可以考虑。",
    )

    findings = audit_parsed_product_copy(parse_markdown_text(source), source_text=source)

    repeated = next(item for item in findings if item["rule_id"] == "repeated_abstract_closing_form")
    assert len(repeated["locations"]) == 3
    assert {item["uid"] for item in repeated["locations"]} == {"P001", "P002", "P003"}


def test_audit_detects_new_abstract_closing_shell_without_adding_a_phrase_to_profile():
    source = _document("采用原纱工艺。如果主要是日常通勤，这一件没毛病。")

    findings = audit_parsed_product_copy(parse_markdown_text(source), source_text=source)

    finding = next(item for item in findings if item["rule_id"] == "abstract_evaluative_closing")
    assert finding["uid"] == "P001"
    assert finding["match"] == "这一件没毛病"


def test_diagnose_product_copy_audit_is_non_blocking_but_reports_cleanliness(tmp_path: Path):
    db = Database(tmp_path / "copy-audit.db")
    md_path = tmp_path / "episode.md"
    project_id = db.upsert_project(
        {
            "name": "家居-防晒衣",
            "scheme_id": "scheme-1",
            "scheme_name": "主方案",
            "md_path": str(md_path),
        }
    )
    md_path.write_text(_document("防护参数明确。京东京造这件很实在。"), encoding="utf-8")

    result = diagnose_product_copy_audit(db, project_id=project_id)

    assert result["ok"] is True
    assert result["clean"] is False
    assert result["summary"]["flagged_variants"] == 1
    assert result["findings"][0]["rule_id"] == "voice_phrase_rejected"
