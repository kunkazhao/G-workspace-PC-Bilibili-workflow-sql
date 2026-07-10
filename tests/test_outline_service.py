from pathlib import Path

import pytest

from bworkflow_sql.db import Database
from bworkflow_sql.master_contracts import (
    MasterCategoryIdentity,
    MasterContractError,
    MasterPriceRange,
    MasterSchemeIdentity,
    MasterSchemeSnapshot,
    MasterWorkspace,
)
from bworkflow_sql.outline_service import OutlineService
from bworkflow_sql.repositories import Repository


def _price_snapshot(*ranges):
    return MasterSchemeSnapshot(
        schema_version="1.0.0",
        generated_at_utc="2026-07-10T12:00:00Z",
        snapshot_id="sha256:" + "a" * 64,
        workspace=MasterWorkspace(id="workspace-1", name="赵二", slug="zhaoer"),
        scheme=MasterSchemeIdentity(
            id="scheme-1",
            name="主方案",
            category=MasterCategoryIdentity(id="category-1", name="桌面音响"),
            updated_at=None,
        ),
        price_ranges=tuple(ranges),
        products=(),
    )


class FakeMasterContracts:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def fetch_scheme_snapshot(self, workspace_id, scheme_id, *, force_refresh=False):
        self.calls.append((workspace_id, scheme_id, force_refresh))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_outline_price_ranges_use_only_snapshot_contract_fields(tmp_path: Path):
    db = Database(tmp_path / "ranges.db")
    adapter = FakeMasterContracts(
        _price_snapshot(
            MasterPriceRange(min_amount=None, max_amount="100", label="100元以下"),
            MasterPriceRange(min_amount="100", max_amount=None, label="100元以上"),
        )
    )
    service = OutlineService(db, master_contracts=adapter)

    ranges = service.fetch_scheme_price_ranges(
        {"workspace_id": "workspace-1", "scheme_id": "scheme-1"}
    )

    assert ranges == [
        {"min": None, "max": "100"},
        {"min": "100", "max": None},
    ]
    assert adapter.calls == [("workspace-1", "scheme-1", True)]
    db.close()


def test_outline_falls_back_only_for_master_unavailable(tmp_path: Path):
    db = Database(tmp_path / "fallback.db")
    adapter = FakeMasterContracts(
        MasterContractError("master_unavailable", "offline", retryable=True)
    )

    ranges = OutlineService(db, master_contracts=adapter).fetch_scheme_price_ranges(
        {"workspace_id": "workspace-1", "scheme_id": "scheme-1"}
    )

    assert ranges == OutlineService.DEFAULT_PRICE_RANGES
    db.close()


@pytest.mark.parametrize(
    "code", ["invalid_master_contract", "unsupported_contract_version"]
)
def test_outline_does_not_hide_integrity_or_version_errors(tmp_path: Path, code: str):
    db = Database(tmp_path / f"{code}.db")
    adapter = FakeMasterContracts(MasterContractError(code, "contract failure"))

    with pytest.raises(MasterContractError) as caught:
        OutlineService(db, master_contracts=adapter).fetch_scheme_price_ranges(
            {"workspace_id": "workspace-1", "scheme_id": "scheme-1"}
        )

    assert caught.value.code == code
    db.close()


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

## 视频信息

保留视频信息

## 商品文案

### 59元-YXEJ002-竹林鸟夜莺Z1
#### 正文
保留商品正文

### 99元-YXEJ999-已删除商品
#### 正文
已删除商品正文

## 价格过渡文案

### 0-100元
#### 正文
保留价格过渡
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
    assert "## 视频信息" not in text
    assert "保留视频信息" not in text
    assert "保留价格过渡" in text
    assert "## 已移出 Master 的商品文案" in text
    assert "已删除商品正文" in text
