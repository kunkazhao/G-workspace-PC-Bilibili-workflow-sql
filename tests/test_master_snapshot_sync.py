from __future__ import annotations

from dataclasses import replace
import importlib
import inspect
import json

import pytest

from bworkflow_sql import master_contracts as contracts


def _planner():
    try:
        return importlib.import_module("bworkflow_sql.master_snapshot_sync")
    except ModuleNotFoundError:
        pytest.fail("bworkflow_sql.master_snapshot_sync must provide the pure planner")


def _product(
    *,
    uid="SP001",
    title="音响A",
    amount="99.9",
    display="99.9元",
    sort_order=1,
    master_item_id="item-1",
    cover_url="https://example.invalid/a.jpg",
    remark="近场听感均衡",
    slots=(contracts.MasterSpecSlot(label="连接方式", value="蓝牙/USB"),),
    template_id="xiaobo1",
    tags=("桌面",),
    featured=True,
):
    return contracts.MasterSnapshotProduct(
        master_item_id=master_item_id,
        uid=uid,
        title=title,
        sort_order=sort_order,
        price=contracts.MasterMoney(
            amount=amount,
            currency="CNY",
            source="jd",
            display=display,
        ),
        card=contracts.MasterProductCard(
            cover_url=cover_url,
            remark=remark,
            spec_slots=slots,
            template_id=template_id,
        ),
        tags=tags,
        featured=featured,
        source_updated_at="2026-07-07T08:00:00Z",
    )


def _snapshot(*products, workspace_id="workspace-1", scheme_id="scheme-1", category_id="category-1"):
    return contracts.MasterSchemeSnapshot(
        schema_version="1.0.0",
        generated_at_utc="2026-07-10T12:00:00Z",
        snapshot_id="sha256:" + "a" * 64,
        workspace=contracts.MasterWorkspace(
            id=workspace_id,
            name="赵二",
            slug="zhaoer",
        ),
        scheme=contracts.MasterSchemeIdentity(
            id=scheme_id,
            name="主方案",
            category=contracts.MasterCategoryIdentity(
                id=category_id,
                name="桌面音响",
            ),
            updated_at="2026-07-07T08:17:05Z",
        ),
        price_ranges=(
            contracts.MasterPriceRange(
                min_amount=None,
                max_amount="100",
                label="100元以下",
            ),
        ),
        products=tuple(products),
    )


def _project(**changes):
    return {
        "id": 23,
        "name": "数码-桌面音响",
        "workspace_id": "workspace-1",
        "category_id": "category-1",
        "scheme_id": "scheme-1",
        **changes,
    }


def _local(product=None, **changes):
    planner = _planner()
    record = planner.normalize_snapshot_product(product or _product(), project_id=23)
    return {
        "project_id": 23,
        "uid": record.uid,
        "title": record.title,
        "price_label": record.price_label,
        "sort_order": record.sort_order,
        "master_item_id": record.master_item_id,
        "product_card_json": record.product_card_json,
        "active": 1,
        "removed_from_master": 0,
        **changes,
    }


def test_add_plan_contains_complete_normalized_record_and_one_canonical_card_mapping():
    planner = _planner()
    product = _product()

    plan = planner.plan_master_snapshot_sync(_project(), [], _snapshot(product))

    assert plan.snapshot_id == "sha256:" + "a" * 64
    assert plan.project_id == 23
    assert len(plan.added) == 1
    assert plan.updated == ()
    assert plan.removed == ()
    assert plan.reactivated == ()
    record = plan.added[0].after
    assert record is not None
    assert record.uid == "SP001"
    assert record.price_label == "99.9元"
    assert record.master_item_id == "item-1"
    card = json.loads(record.product_card_json)
    assert card == {
        "coverAsset": "https://example.invalid/a.jpg",
        "dataMap": {
            "cover": "https://example.invalid/a.jpg",
            "price": "99.9元",
            "remark": "近场听感均衡",
            "title": "音响A",
        },
        "featured": True,
        "slots": [{"label": "连接方式", "value": "蓝牙/USB"}],
        "tags": ["桌面"],
        "templateId": "xiaobo1",
    }
    assert "cover_url" not in record.product_card_json
    assert "coverUrl" not in record.product_card_json


@pytest.mark.parametrize(
    ("changed_product", "expected_field"),
    [
        (_product(title="音响A Pro"), "title"),
        (_product(amount="109", display="109元"), "price_label"),
        (_product(remark="更适合桌面近场"), "product_card_json"),
        (_product(tags=("桌面", "进阶")), "product_card_json"),
        (
            _product(
                slots=(
                    contracts.MasterSpecSlot(label="连接方式", value="蓝牙/USB"),
                    contracts.MasterSpecSlot(label="额定功率", value="20W"),
                )
            ),
            "product_card_json",
        ),
    ],
)
def test_material_changes_are_reported_in_stable_fields(changed_product, expected_field):
    planner = _planner()
    plan = planner.plan_master_snapshot_sync(
        _project(),
        [_local()],
        _snapshot(changed_product),
    )

    assert len(plan.updated) == 1
    assert plan.updated[0].changed_fields == (expected_field,)


def test_order_only_change_does_not_masquerade_as_content_change():
    planner = _planner()
    changed = _product(sort_order=2)

    plan = planner.plan_master_snapshot_sync(
        _project(), [_local()], _snapshot(changed)
    )

    assert plan.updated[0].changed_fields == ("sort_order",)


def test_stale_derived_title_inside_local_card_is_repaired_as_card_drift():
    planner = _planner()
    local = _local()
    card = json.loads(local["product_card_json"])
    card["dataMap"]["title"] = "卡片里的旧标题"
    local["product_card_json"] = json.dumps(card, ensure_ascii=False)

    plan = planner.plan_master_snapshot_sync(
        _project(), [local], _snapshot(_product())
    )

    assert plan.updated[0].changed_fields == ("product_card_json",)


def test_missing_active_product_is_soft_removed_without_losing_history():
    planner = _planner()
    local = _local()

    plan = planner.plan_master_snapshot_sync(_project(), [local], _snapshot())

    assert len(plan.removed) == 1
    change = plan.removed[0]
    assert change.before is not None and change.after is not None
    assert change.before.title == change.after.title == "音响A"
    assert change.after.active == 0
    assert change.after.removed_from_master == 1
    assert change.changed_fields == ("active", "removed_from_master")


def test_removed_product_is_reactivated_and_repairs_current_snapshot_fields():
    planner = _planner()
    local = _local(active=0, removed_from_master=1, title="旧标题")

    plan = planner.plan_master_snapshot_sync(
        _project(), [local], _snapshot(_product())
    )

    assert len(plan.reactivated) == 1
    assert plan.reactivated[0].changed_fields == (
        "title",
        "active",
        "removed_from_master",
    )
    assert plan.reactivated[0].after.active == 1
    assert plan.reactivated[0].after.removed_from_master == 0


def test_unchanged_and_already_removed_rows_produce_no_writes():
    planner = _planner()
    active = _local()
    removed = _local(
        _product(uid="SP999", master_item_id="item-999"),
        active=0,
        removed_from_master=1,
    )

    plan = planner.plan_master_snapshot_sync(
        _project(), [active, removed], _snapshot(_product())
    )

    assert not plan.has_changes
    assert plan.change_count == 0
    assert [change.uid for change in plan.unchanged] == ["SP001"]
    assert [change.uid for change in plan.historical_unchanged] == ["SP999"]


def test_empty_snapshot_soft_removes_only_rows_that_are_currently_active():
    planner = _planner()
    active = _local()
    removed = _local(
        _product(uid="SP999", master_item_id="item-999"),
        active=0,
        removed_from_master=1,
    )

    plan = planner.plan_master_snapshot_sync(
        _project(), [removed, active], _snapshot()
    )

    assert [change.uid for change in plan.removed] == ["SP001"]
    assert plan.unchanged == ()
    assert [change.uid for change in plan.historical_unchanged] == ["SP999"]


@pytest.mark.parametrize(
    ("project_change", "snapshot_change", "field"),
    [
        ({"workspace_id": "workspace-2"}, {}, "workspace_id"),
        ({"scheme_id": "scheme-2"}, {}, "scheme_id"),
        ({"category_id": "category-2"}, {}, "category_id"),
    ],
)
def test_wrong_project_identity_fails_closed(project_change, snapshot_change, field):
    planner = _planner()

    with pytest.raises(planner.MasterSnapshotPlanError) as caught:
        planner.plan_master_snapshot_sync(
            _project(**project_change),
            [],
            _snapshot(_product(), **snapshot_change),
        )

    assert caught.value.code == "project_identity_mismatch"
    assert caught.value.details["field"] == field


def test_duplicate_local_uid_is_rejected_before_diffing():
    planner = _planner()
    local = _local()

    with pytest.raises(planner.MasterSnapshotPlanError) as caught:
        planner.plan_master_snapshot_sync(
            _project(), [local, dict(local)], _snapshot(_product())
        )

    assert caught.value.code == "duplicate_local_uid"
    assert caught.value.details == {"uids": ["SP001"]}


def test_changed_field_order_is_stable_and_plan_is_deterministic():
    planner = _planner()
    local = _local(
        title="旧标题",
        price_label="88元",
        sort_order=9,
        master_item_id="old-item",
        product_card_json="{}",
    )
    snapshot = _snapshot(_product())

    first = planner.plan_master_snapshot_sync(_project(), [local], snapshot)
    second = planner.plan_master_snapshot_sync(_project(), [dict(local)], snapshot)

    assert first == second
    assert first.updated[0].changed_fields == (
        "title",
        "price_label",
        "master_item_id",
        "product_card_json",
        "sort_order",
    )


def test_planner_module_has_no_io_database_network_or_clock_imports():
    source = inspect.getsource(_planner())

    assert "from .db" not in source
    assert "import requests" not in source
    assert "pathlib" not in source
    assert "datetime" not in source
    assert "time" not in source
    assert "now_iso" not in source
