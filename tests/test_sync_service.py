from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.utils import text_hash


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


def test_script_hash_matches_legacy_voice_registry_format(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project({"name": "hash-test"})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59元"}],
    )
    parsed = parse_markdown_text(
        """
## 商品文案

### 59元-YXEJ002-竹林鸟夜莺Z1
#### 正文
正常配音文案
""".strip()
    )

    SyncService(db).sync_markdown_payload(project_id, parsed)
    row = db.fetchone("SELECT text_hash FROM script_blocks WHERE project_id=? AND owner_uid=?", (project_id, "YXEJ002"))

    assert row["text_hash"] == text_hash("正常配音文案")
    assert len(row["text_hash"]) == 40
