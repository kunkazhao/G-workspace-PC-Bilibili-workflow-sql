from __future__ import annotations

from pathlib import Path

import pytest

from bworkflow_sql.db import Database
from bworkflow_sql.product_copy_lint import diagnose_product_copy_lint, lint_product_copy


@pytest.mark.parametrize(
    ("text", "rule_id"),
    [
        ("也是这期一百到两百档的主推。", "internal_price_tier"),
        ("是这期百元档里我更愿意推荐的一件。", "internal_price_tier"),
        ("也是这期一百到两百档的主推。", "internal_role_label"),
        ("页面标注 UPF 一百加。", "source_page_reference"),
        ("根据页面，这款采用原纱工艺。", "source_page_reference"),
        ("资料显示这款透气性不错。", "source_attribution_phrase"),
        ("商品页有部分买家反馈尺码偏大。", "source_page_reference"),
    ],
)
def test_lint_product_copy_rejects_internal_and_research_process_language(text: str, rule_id: str):
    assert rule_id in {finding.rule_id for finding in lint_product_copy(text)}


@pytest.mark.parametrize(
    "text",
    [
        "预算在一百到两百元，想兼顾防护和透气，这件可以优先看。",
        "这个价位更适合看重透气和版型的人。",
        "UPF 做到一百加，日常通勤和短途户外都够用。",
        "如果你主要看一百多元预算，可以先看这件。",
    ],
)
def test_lint_product_copy_allows_budget_and_natural_recommendation_language(text: str):
    assert lint_product_copy(text) == []


def test_diagnose_product_copy_lint_reports_product_variant_and_markdown_line(tmp_path: Path):
    db = Database(tmp_path / "copy-lint.db")
    md_path = tmp_path / "episode.md"
    project_id = db.upsert_project(
        {
            "name": "家居-防晒衣",
            "scheme_id": "scheme-1",
            "scheme_name": "主方案",
            "md_path": str(md_path),
        }
    )
    md_path.write_text(
        """
## 商品文案

### 178元-FSY033-海上公路轻薄男款

#### 正文

海上公路轻薄男款，也是这期一百到两百档的主推。页面标注 UPF 一百加。
""".strip(),
        encoding="utf-8",
    )

    result = diagnose_product_copy_lint(db, project_id=project_id)

    assert result["ok"] is False
    assert result["summary"] == {
        "products_scanned": 1,
        "variants_scanned": 1,
        "failed_products": 1,
        "findings": 3,
    }
    assert {finding["rule_id"] for finding in result["findings"]} == {
        "internal_role_label",
        "internal_price_tier",
        "source_page_reference",
    }
    assert all(finding["uid"] == "FSY033" for finding in result["findings"])
    assert all(finding["block_label"] == "正文" for finding in result["findings"])
    assert all(finding["line"] == 7 for finding in result["findings"])
