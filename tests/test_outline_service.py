from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.outline_service import OutlineService
from bworkflow_sql.repositories import Repository


def test_outline_uses_price_uid_title_and_preserves_existing_copy(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "数码-有线耳机",
            "category_parent_name": "数码",
            "category_name": "有线耳机",
            "scheme_name": "主方案",
        }
    )
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59.0"},
            {"uid": "YXEJ003", "title": "KZ Gale疾风", "price_label": "79元"},
        ],
    )
    target = tmp_path / "数码-有线耳机.md"
    target.write_text(
        """
## 引言文案

### 引言1
保留引言

## 商品文案

### 59元-YXEJ002-竹林鸟夜莺Z1
#### 正文
保留商品正文

## 价格过渡文案
""".strip(),
        encoding="utf-8",
    )

    result = OutlineService(db).init_or_update_outline(project_id, target)
    text = target.read_text(encoding="utf-8")

    assert len(result["added"]) == 1
    assert len(result["preserved"]) == 1
    assert "### 59元-YXEJ002-竹林鸟夜莺Z1" in text
    assert "### 79元-YXEJ003-KZ Gale疾风" in text
    assert "#### 正文" in text
    assert "59.0-YXEJ002" not in text
    assert "保留商品正文" in text
    assert "保留引言" in text
