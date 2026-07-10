from __future__ import annotations

import json
from pathlib import Path

import pytest

from bworkflow_sql import master_contracts as contracts
from bworkflow_sql.db import Database
from bworkflow_sql.master_snapshot_sync import plan_master_snapshot_sync
from bworkflow_sql.repositories import Repository
from bworkflow_sql.sync_service import MasterSyncError, SyncService


def _product(*, title="音响A", sort_order=1, remark="近场听感均衡"):
    return contracts.MasterSnapshotProduct(
        master_item_id="item-1",
        uid="SP001",
        title=title,
        sort_order=sort_order,
        price=contracts.MasterMoney(
            amount="99.9", currency="CNY", source="jd", display="99.9元"
        ),
        card=contracts.MasterProductCard(
            cover_url="https://example.invalid/a.jpg",
            remark=remark,
            spec_slots=(
                contracts.MasterSpecSlot(label="连接方式", value="蓝牙/USB"),
            ),
            template_id="xiaobo1",
        ),
        tags=("桌面",),
        featured=True,
        source_updated_at="2026-07-07T08:00:00Z",
    )


def _snapshot(*products, marker="a"):
    return contracts.MasterSchemeSnapshot(
        schema_version="1.0.0",
        generated_at_utc="2026-07-10T12:00:00Z",
        snapshot_id="sha256:" + marker * 64,
        workspace=contracts.MasterWorkspace(
            id="workspace-1", name="赵二", slug="zhaoer"
        ),
        scheme=contracts.MasterSchemeIdentity(
            id="scheme-1",
            name="主方案",
            category=contracts.MasterCategoryIdentity(
                id="category-1", name="桌面音响"
            ),
            updated_at="2026-07-07T08:17:05Z",
        ),
        price_ranges=(
            contracts.MasterPriceRange(
                min_amount=None, max_amount="100", label="100元以下"
            ),
        ),
        products=tuple(products),
    )


def _project(db: Database) -> int:
    return db.upsert_project(
        {
            "name": "数码-桌面音响",
            "workspace_id": "workspace-1",
            "category_id": "category-1",
            "category_name": "桌面音响",
            "scheme_id": "scheme-1",
            "scheme_name": "主方案",
        }
    )


class FakeAdapter:
    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def fetch_scheme_snapshot(
        self, workspace_id, scheme_id, *, force_refresh=False
    ):
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "scheme_id": scheme_id,
                "force_refresh": force_refresh,
            }
        )
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _seed_snapshot(repo: Repository, project_id: int, snapshot) -> None:
    plan = plan_master_snapshot_sync(
        repo.project(project_id), repo.products(project_id), snapshot
    )
    repo.apply_master_snapshot_plan(plan, applied_at="2026-07-10T11:00:00Z")


def test_preview_fetches_fresh_snapshot_reports_card_and_order_and_writes_nothing(tmp_path: Path):
    db = Database(tmp_path / "preview.db")
    repo = Repository(db)
    project_id = _project(db)
    _seed_snapshot(repo, project_id, _snapshot(_product(), marker="a"))
    changed = _snapshot(
        _product(sort_order=2, remark="更适合近场桌面"), marker="b"
    )
    adapter = FakeAdapter(changed)
    before_project = repo.project(project_id)
    before_products = repo.products(project_id)
    before_events = db.fetchall("SELECT * FROM sync_events WHERE project_id=?", (project_id,))

    result = SyncService(db, master_contracts=adapter).sync_master_scheme(
        project_id, apply_changes=False
    )

    assert adapter.calls == [
        {
            "workspace_id": "workspace-1",
            "scheme_id": "scheme-1",
            "force_refresh": True,
        }
    ]
    assert result["snapshot_id"] == changed.snapshot_id
    assert result["updated"][0]["changed_fields"] == [
        "product_card_json",
        "sort_order",
    ]
    assert repo.project(project_id) == before_project
    assert repo.products(project_id) == before_products
    assert db.fetchall("SELECT * FROM sync_events WHERE project_id=?", (project_id,)) == before_events
    db.close()


def test_apply_fetches_once_and_applies_that_complete_plan(tmp_path: Path):
    db = Database(tmp_path / "apply.db")
    repo = Repository(db)
    project_id = _project(db)
    snapshot = _snapshot(_product(), marker="a")
    adapter = FakeAdapter(snapshot)

    result = SyncService(db, master_contracts=adapter).sync_master_scheme(project_id)

    assert len(adapter.calls) == 1
    assert result["snapshot_id"] == snapshot.snapshot_id
    assert result["change_count"] == 1
    assert result["added"][0]["uid"] == "SP001"
    assert repo.project(project_id)["master_snapshot_id"] == snapshot.snapshot_id
    assert json.loads(repo.products(project_id)[0]["product_card_json"])["tags"] == ["桌面"]
    db.close()


def test_expected_snapshot_mismatch_is_stale_preview_and_has_zero_writes(tmp_path: Path):
    db = Database(tmp_path / "stale.db")
    repo = Repository(db)
    project_id = _project(db)
    adapter = FakeAdapter(_snapshot(_product(), marker="b"))
    service = SyncService(db, master_contracts=adapter)

    with pytest.raises(MasterSyncError) as caught:
        service.sync_master_scheme(
            project_id,
            expected_snapshot_id="sha256:" + "a" * 64,
        )

    assert caught.value.code == "stale_master_preview"
    assert repo.products(project_id) == []
    assert repo.project(project_id)["master_snapshot_id"] is None
    assert db.fetchall("SELECT * FROM sync_events WHERE project_id=?", (project_id,)) == []
    db.close()


def test_same_snapshot_id_still_repairs_local_drift_through_service(tmp_path: Path):
    db = Database(tmp_path / "drift.db")
    repo = Repository(db)
    project_id = _project(db)
    snapshot = _snapshot(_product(), marker="a")
    _seed_snapshot(repo, project_id, snapshot)
    db.execute(
        "UPDATE products SET title='本地漂移' WHERE project_id=? AND uid='SP001'",
        (project_id,),
    )
    adapter = FakeAdapter(snapshot)

    result = SyncService(db, master_contracts=adapter).sync_master_scheme(
        project_id,
        expected_snapshot_id=snapshot.snapshot_id,
    )

    assert len(result["updated"]) == 1
    assert repo.products(project_id)[0]["title"] == "音响A"
    db.close()


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("master_unavailable", "无法连接 Master 契约服务。"),
        ("invalid_master_contract", "Master 数据契约损坏。"),
        ("unsupported_contract_version", "Master 契约版本不受支持。"),
    ],
)
def test_transport_integrity_and_version_errors_remain_typed_and_clear(
    tmp_path: Path, code: str, message: str
):
    db = Database(tmp_path / f"{code}.db")
    project_id = _project(db)
    error = contracts.MasterContractError(code, message, retryable=code == "master_unavailable")

    with pytest.raises(contracts.MasterContractError) as caught:
        SyncService(db, master_contracts=FakeAdapter(error)).sync_master_scheme(
            project_id, apply_changes=False
        )

    assert caught.value.code == code
    assert str(caught.value) == message
    db.close()
