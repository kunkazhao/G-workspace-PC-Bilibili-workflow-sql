from __future__ import annotations

from pathlib import Path

import pytest

from bworkflow_sql import master_contracts as contracts
from bworkflow_sql.db import Database
from bworkflow_sql.master_snapshot_sync import plan_master_snapshot_sync
from bworkflow_sql.repositories import Repository


def _product(
    *,
    uid="SP001",
    title="音响A",
    master_item_id="item-1",
    sort_order=1,
):
    return contracts.MasterSnapshotProduct(
        master_item_id=master_item_id,
        uid=uid,
        title=title,
        sort_order=sort_order,
        price=contracts.MasterMoney(
            amount="99.9",
            currency="CNY",
            source="jd",
            display="99.9元",
        ),
        card=contracts.MasterProductCard(
            cover_url="https://example.invalid/a.jpg",
            remark="近场听感均衡",
            spec_slots=(
                contracts.MasterSpecSlot(label="连接方式", value="蓝牙/USB"),
            ),
            template_id="xiaobo1",
        ),
        tags=("桌面",),
        featured=True,
        source_updated_at="2026-07-07T08:00:00Z",
    )


def _snapshot(*products, snapshot_id=None):
    return contracts.MasterSchemeSnapshot(
        schema_version="1.0.0",
        generated_at_utc="2026-07-10T12:00:00Z",
        snapshot_id=snapshot_id or ("sha256:" + "a" * 64),
        workspace=contracts.MasterWorkspace(
            id="workspace-1",
            name="赵二",
            slug="zhaoer",
        ),
        scheme=contracts.MasterSchemeIdentity(
            id="scheme-1",
            name="主方案",
            category=contracts.MasterCategoryIdentity(
                id="category-1",
                name="桌面音响",
            ),
            updated_at="2026-07-07T08:17:05Z",
        ),
        price_ranges=(),
        products=tuple(products),
    )


def _project_payload(**changes):
    return {
        "name": "数码-桌面音响",
        "workspace_id": "workspace-1",
        "workspace_name": "赵二",
        "category_id": "category-1",
        "category_name": "桌面音响",
        "scheme_id": "scheme-1",
        "scheme_name": "主方案",
        **changes,
    }


def _plan(repo: Repository, project_id: int, snapshot):
    project = repo.project(project_id)
    return plan_master_snapshot_sync(
        project,
        repo.products(project_id),
        snapshot,
    )


def test_apply_writes_products_provenance_event_and_items_atomically(tmp_path: Path):
    db = Database(tmp_path / "apply.db")
    repo = Repository(db)
    project_id = db.upsert_project(_project_payload())
    snapshot = _snapshot(
        _product(),
        _product(
            uid="SP002",
            title="音响B",
            master_item_id="item-2",
            sort_order=2,
        ),
    )
    plan = _plan(repo, project_id, snapshot)

    result = repo.apply_master_snapshot_plan(
        plan,
        applied_at="2026-07-10T12:30:00Z",
    )

    products = repo.products(project_id)
    project = repo.project(project_id)
    events = db.fetchall("SELECT * FROM sync_events WHERE project_id=?", (project_id,))
    event_items = db.fetchall(
        "SELECT * FROM sync_event_items WHERE sync_event_id=? ORDER BY id",
        (events[0]["id"],),
    )
    assert [item["uid"] for item in products] == ["SP001", "SP002"]
    assert project["master_snapshot_id"] == snapshot.snapshot_id
    assert project["master_snapshot_applied_at"] == "2026-07-10T12:30:00Z"
    assert len(events) == 1
    assert events[0]["event_type"] == "master_snapshot_sync"
    assert [item["status"] for item in event_items] == ["added", "added"]
    assert result["snapshot_id"] == snapshot.snapshot_id
    assert len(result["added"]) == 2
    assert result["event_id"] == events[0]["id"]
    db.close()


def test_noop_apply_updates_provenance_and_event_but_not_product_row(tmp_path: Path):
    db = Database(tmp_path / "noop.db")
    repo = Repository(db)
    project_id = db.upsert_project(_project_payload())
    snapshot = _snapshot(_product())
    repo.apply_master_snapshot_plan(
        _plan(repo, project_id, snapshot),
        applied_at="2026-07-10T12:00:00Z",
    )
    before = repo.products(project_id)[0]

    plan = _plan(repo, project_id, snapshot)
    result = repo.apply_master_snapshot_plan(
        plan,
        applied_at="2026-07-10T13:00:00Z",
    )

    after = repo.products(project_id)[0]
    project = repo.project(project_id)
    events = db.fetchall("SELECT id FROM sync_events WHERE project_id=?", (project_id,))
    latest_items = db.fetchall(
        "SELECT * FROM sync_event_items WHERE sync_event_id=?",
        (events[-1]["id"],),
    )
    assert not plan.has_changes
    assert result["change_count"] == 0
    assert before["updated_at"] == after["updated_at"]
    assert project["master_snapshot_applied_at"] == "2026-07-10T13:00:00Z"
    assert len(events) == 2
    assert latest_items == []
    db.close()


def test_product_failure_rolls_back_rows_provenance_and_event(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "product-failure.db")
    repo = Repository(db)
    project_id = db.upsert_project(_project_payload())
    plan = _plan(
        repo,
        project_id,
        _snapshot(
            _product(),
            _product(uid="SP002", master_item_id="item-2", sort_order=2),
        ),
    )
    original = repo._apply_snapshot_change
    calls = 0

    def fail_second(conn, change, applied_at):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected product failure")
        return original(conn, change, applied_at)

    monkeypatch.setattr(repo, "_apply_snapshot_change", fail_second)

    with pytest.raises(RuntimeError, match="injected product failure"):
        repo.apply_master_snapshot_plan(plan, applied_at="2026-07-10T12:30:00Z")

    assert repo.products(project_id) == []
    project = repo.project(project_id)
    assert project["master_snapshot_id"] is None
    assert db.fetchall("SELECT * FROM sync_events WHERE project_id=?", (project_id,)) == []
    db.close()


def test_event_failure_rolls_back_product_and_provenance(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "event-failure.db")
    repo = Repository(db)
    project_id = db.upsert_project(_project_payload())
    plan = _plan(repo, project_id, _snapshot(_product()))

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(repo, "_insert_snapshot_event", fail_event)

    with pytest.raises(RuntimeError, match="injected event failure"):
        repo.apply_master_snapshot_plan(plan, applied_at="2026-07-10T12:30:00Z")

    assert repo.products(project_id) == []
    project = repo.project(project_id)
    assert project["master_snapshot_id"] is None
    assert db.fetchall("SELECT * FROM sync_events WHERE project_id=?", (project_id,)) == []
    db.close()


def test_soft_removal_preserves_product_id_card_and_script_history(tmp_path: Path):
    db = Database(tmp_path / "remove.db")
    repo = Repository(db)
    project_id = db.upsert_project(_project_payload())
    snapshot = _snapshot(_product())
    repo.apply_master_snapshot_plan(
        _plan(repo, project_id, snapshot),
        applied_at="2026-07-10T12:00:00Z",
    )
    before = repo.products(project_id)[0]
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO script_blocks (
                project_id, script_type, owner_uid, block_label, body,
                created_at, updated_at
            ) VALUES (?, 'product', ?, '正文', '历史文案', 'before', 'before')
            """,
            (project_id, before["uid"]),
        )

    removal_plan = _plan(repo, project_id, _snapshot())
    result = repo.apply_master_snapshot_plan(
        removal_plan,
        applied_at="2026-07-10T13:00:00Z",
    )

    after = repo.products(project_id)[0]
    scripts = repo.script_blocks(project_id)
    assert len(result["removed"]) == 1
    assert after["id"] == before["id"]
    assert after["title"] == before["title"]
    assert after["product_card_json"] == before["product_card_json"]
    assert after["active"] == 0
    assert after["removed_from_master"] == 1
    assert len(scripts) == 1
    assert scripts[0]["body"] == "历史文案"
    db.close()


def test_same_snapshot_id_still_repairs_local_drift(tmp_path: Path):
    db = Database(tmp_path / "drift.db")
    repo = Repository(db)
    project_id = db.upsert_project(_project_payload())
    snapshot = _snapshot(_product())
    repo.apply_master_snapshot_plan(
        _plan(repo, project_id, snapshot),
        applied_at="2026-07-10T12:00:00Z",
    )
    db.execute(
        "UPDATE products SET title='本地漂移' WHERE project_id=? AND uid='SP001'",
        (project_id,),
    )

    drift_plan = _plan(repo, project_id, snapshot)
    result = repo.apply_master_snapshot_plan(
        drift_plan,
        applied_at="2026-07-10T13:00:00Z",
    )

    assert drift_plan.snapshot_id == repo.project(project_id)["master_snapshot_id"]
    assert len(result["updated"]) == 1
    assert repo.products(project_id)[0]["title"] == "音响A"
    db.close()
