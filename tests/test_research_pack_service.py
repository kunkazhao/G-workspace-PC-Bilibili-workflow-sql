from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.repositories import Repository
from bworkflow_sql.research_pack_service import ResearchPackService


def test_research_pack_uses_category_and_scheme_path(tmp_path: Path, monkeypatch):
    import bworkflow_sql.research_pack_service as module

    monkeypatch.setattr(module, "DEFAULT_RESEARCH_PACK_ROOT", tmp_path / "packs")
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "数码-桌面音响",
            "category_parent_name": "数码",
            "category_name": "桌面音响",
            "category_id": "cat-1",
            "scheme_id": "scheme-1",
            "scheme_name": "主方案",
        }
    )
    repo.upsert_products_from_master(
        project_id,
        [
                {
                    "uid": "ZMYX001",
                    "title": "七彩虹 CF100",
                    "price_label": "62.0",
                    "spec": {"输出功率": "4W+4W"},
                }
        ],
    )

    result = ResearchPackService(db).init_or_update_pack(project_id)
    target = Path(result["target_path"])
    text = target.read_text(encoding="utf-8")

    assert target == tmp_path / "packs" / "数码-桌面音响" / "主方案.md"
    assert result["added"][0]["uid"] == "ZMYX001"
    assert "# 数码-桌面音响｜主方案｜资料采集包" in text
    assert "## ZMYX001｜七彩虹 CF100" in text
    assert "- 输出功率：4W+4W" in text
    assert "### 联网可确认参数" in text
    assert "### 来源" in text
    assert "### 写作可用判断" in text


def test_research_pack_preserves_existing_product_notes(tmp_path: Path, monkeypatch):
    import bworkflow_sql.research_pack_service as module

    monkeypatch.setattr(module, "DEFAULT_RESEARCH_PACK_ROOT", tmp_path / "packs")
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "数码-桌面音响",
            "scheme_name": "主方案",
        }
    )
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "ZMYX001", "title": "七彩虹 CF100", "price_label": "62.0"},
            {"uid": "ZMYX002", "title": "漫步者 G1200", "price_label": "134.0"},
        ],
    )
    target = tmp_path / "packs" / "数码-桌面音响" / "主方案.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        """
# old

## ZMYX001｜七彩虹 CF100

### 来源
- 来源1：https://example.com/cf100
""".strip(),
        encoding="utf-8",
    )

    result = ResearchPackService(db).init_or_update_pack(project_id)
    text = target.read_text(encoding="utf-8")

    assert len(result["added"]) == 1
    assert len(result["preserved"]) == 1
    assert "https://example.com/cf100" in text
    assert "## ZMYX002｜漫步者 G1200" in text
