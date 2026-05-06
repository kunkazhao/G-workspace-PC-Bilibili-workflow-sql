from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.sync_service import SyncService


def test_markdown_sync_only_imports_master_products(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project({"name": "test"})
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59元"},
            {"uid": "YXEJ003", "title": "KZ Gale疾风", "price_label": "79元"},
        ],
    )
    parsed = parse_markdown_text(
        """
## 商品文案

### 竹林鸟夜莺Z1-YXEJ002-59元
正文 A

### 多余商品-YXEJ999-999元
不应该导入
""".strip()
    )
    result = SyncService(db).sync_markdown_payload(project_id, parsed)
    blocks = repo.script_blocks(project_id)
    assert result["upserted"] == 1
    assert len(result["extra_md"]) == 1
    assert len(result["missing_copy"]) == 1
    assert blocks[0]["owner_uid"] == "YXEJ002"
