from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import json
from typing import Any


_FIELD_ORDER = (
    "title",
    "price_label",
    "master_item_id",
    "product_card_json",
    "sort_order",
    "active",
    "removed_from_master",
)
_INSERT_FIELD_ORDER = ("uid", *_FIELD_ORDER)


class MasterSnapshotPlanError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class ProductState:
    project_id: int
    uid: str
    title: str
    price_label: str
    sort_order: int
    master_item_id: str
    product_card_json: str
    active: int
    removed_from_master: int


@dataclass(frozen=True, slots=True)
class ProductChange:
    action: str
    uid: str
    before: ProductState | None
    after: ProductState | None
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MasterSnapshotSyncPlan:
    project_id: int
    snapshot_id: str
    workspace_id: str
    category_id: str
    scheme_id: str
    records: tuple[ProductState, ...]
    added: tuple[ProductChange, ...]
    updated: tuple[ProductChange, ...]
    removed: tuple[ProductChange, ...]
    reactivated: tuple[ProductChange, ...]
    unchanged: tuple[ProductChange, ...]

    @property
    def changes(self) -> tuple[ProductChange, ...]:
        return self.added + self.updated + self.removed + self.reactivated

    @property
    def change_count(self) -> int:
        return len(self.changes)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def canonical_product_card_json(product: Any) -> str:
    title = _required_text(product.title, field="product.title")
    price_label = _required_text(product.price.display, field="product.price.display")
    card = product.card
    cover = _optional_text(card.cover_url, field="product.card.cover_url")
    remark = _optional_text(card.remark, field="product.card.remark")
    template_id = _optional_text(
        card.template_id, field="product.card.template_id"
    )

    data_map: dict[str, str] = {"title": title, "price": price_label}
    if cover:
        data_map["cover"] = cover
    if remark:
        data_map["remark"] = remark

    slots = [
        {
            "label": _required_text(slot.label, field="product.card.spec_slots.label"),
            "value": _required_text(slot.value, field="product.card.spec_slots.value"),
        }
        for slot in card.spec_slots
    ]
    tags = [
        _required_text(tag, field="product.tags")
        for tag in product.tags
    ]
    if not isinstance(product.featured, bool):
        raise _invalid_projection("product.featured")

    payload: dict[str, Any] = {
        "dataMap": data_map,
        "slots": slots,
        "tags": tags,
        "featured": product.featured,
    }
    if template_id:
        payload["templateId"] = template_id
    if cover:
        payload["coverAsset"] = cover
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_snapshot_product(product: Any, *, project_id: int) -> ProductState:
    if isinstance(project_id, bool) or not isinstance(project_id, int):
        raise _invalid_projection("project.id")
    sort_order = product.sort_order
    if isinstance(sort_order, bool) or not isinstance(sort_order, int) or sort_order < 1:
        raise _invalid_projection("product.sort_order")
    return ProductState(
        project_id=project_id,
        uid=_required_text(product.uid, field="product.uid"),
        title=_required_text(product.title, field="product.title"),
        price_label=_required_text(
            product.price.display, field="product.price.display"
        ),
        sort_order=sort_order,
        master_item_id=_required_text(
            product.master_item_id, field="product.master_item_id"
        ),
        product_card_json=canonical_product_card_json(product),
        active=1,
        removed_from_master=0,
    )


def plan_master_snapshot_sync(
    project: Mapping[str, Any],
    local_products: Sequence[Mapping[str, Any]],
    snapshot: Any,
) -> MasterSnapshotSyncPlan:
    project_id = _project_id(project.get("id"))
    workspace_id = _required_text(
        project.get("workspace_id"), field="project.workspace_id"
    )
    category_id = _required_text(
        project.get("category_id"), field="project.category_id"
    )
    scheme_id = _required_text(project.get("scheme_id"), field="project.scheme_id")
    _require_identity(
        field="workspace_id",
        project_value=workspace_id,
        snapshot_value=snapshot.workspace.id,
    )
    _require_identity(
        field="category_id",
        project_value=category_id,
        snapshot_value=snapshot.scheme.category.id,
    )
    _require_identity(
        field="scheme_id",
        project_value=scheme_id,
        snapshot_value=snapshot.scheme.id,
    )
    snapshot_id = _required_text(snapshot.snapshot_id, field="snapshot.snapshot_id")

    local_by_uid = _local_index(local_products, project_id=project_id)
    records = tuple(
        sorted(
            (
                normalize_snapshot_product(product, project_id=project_id)
                for product in snapshot.products
            ),
            key=lambda item: (item.sort_order, item.uid),
        )
    )
    duplicate_snapshot_uids = _duplicate_uids(item.uid for item in records)
    if duplicate_snapshot_uids:
        raise MasterSnapshotPlanError(
            "duplicate_snapshot_uid",
            "Master 快照包含重复 UID。",
            details={"uids": duplicate_snapshot_uids},
        )
    incoming_by_uid = {item.uid: item for item in records}

    # validated snapshot + local projection -> normalized records -> complete diff plan
    added: list[ProductChange] = []
    updated: list[ProductChange] = []
    removed: list[ProductChange] = []
    reactivated: list[ProductChange] = []
    unchanged: list[ProductChange] = []

    for after in records:
        before = local_by_uid.get(after.uid)
        if before is None:
            added.append(
                ProductChange(
                    action="add",
                    uid=after.uid,
                    before=None,
                    after=after,
                    changed_fields=_INSERT_FIELD_ORDER,
                )
            )
            continue
        changed_fields = _changed_fields(before, after)
        if not changed_fields:
            unchanged.append(_unchanged(before))
            continue
        action = (
            "reactivate"
            if before.active != 1 or before.removed_from_master != 0
            else "update"
        )
        change = ProductChange(
            action=action,
            uid=after.uid,
            before=before,
            after=after,
            changed_fields=changed_fields,
        )
        (reactivated if action == "reactivate" else updated).append(change)

    missing = sorted(
        (
            item
            for uid, item in local_by_uid.items()
            if uid not in incoming_by_uid
        ),
        key=lambda item: (item.sort_order, item.uid),
    )
    for before in missing:
        after = replace(before, active=0, removed_from_master=1)
        changed_fields = _changed_fields(before, after)
        if changed_fields:
            removed.append(
                ProductChange(
                    action="remove",
                    uid=before.uid,
                    before=before,
                    after=after,
                    changed_fields=changed_fields,
                )
            )
        else:
            unchanged.append(_unchanged(before))

    unchanged.sort(key=lambda change: (change.after.sort_order, change.uid))
    return MasterSnapshotSyncPlan(
        project_id=project_id,
        snapshot_id=snapshot_id,
        workspace_id=workspace_id,
        category_id=category_id,
        scheme_id=scheme_id,
        records=records,
        added=tuple(added),
        updated=tuple(updated),
        removed=tuple(removed),
        reactivated=tuple(reactivated),
        unchanged=tuple(unchanged),
    )


def _local_index(
    rows: Sequence[Mapping[str, Any]], *, project_id: int
) -> dict[str, ProductState]:
    normalized = [_normalize_local(row, project_id=project_id) for row in rows]
    duplicates = _duplicate_uids(item.uid for item in normalized)
    if duplicates:
        raise MasterSnapshotPlanError(
            "duplicate_local_uid",
            "本地商品存在重复 UID。",
            details={"uids": duplicates},
        )
    return {item.uid: item for item in normalized}


def _normalize_local(row: Mapping[str, Any], *, project_id: int) -> ProductState:
    row_project_id = _project_id(row.get("project_id"))
    if row_project_id != project_id:
        raise MasterSnapshotPlanError(
            "project_identity_mismatch",
            "本地商品不属于目标项目。",
            details={
                "field": "local.project_id",
                "project_value": project_id,
                "local_value": row_project_id,
            },
        )
    return ProductState(
        project_id=project_id,
        uid=_required_text(row.get("uid"), field="local.uid"),
        title=_required_text(row.get("title"), field="local.title"),
        price_label=_plain_text(row.get("price_label"), field="local.price_label"),
        sort_order=_integer(row.get("sort_order"), field="local.sort_order"),
        master_item_id=_plain_text(
            row.get("master_item_id"), field="local.master_item_id"
        ),
        product_card_json=_canonical_local_card(row.get("product_card_json")),
        active=_flag(row.get("active"), field="local.active"),
        removed_from_master=_flag(
            row.get("removed_from_master"), field="local.removed_from_master"
        ),
    )


def _canonical_local_card(value: Any) -> str:
    text = _plain_text(value, field="local.product_card_json")
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _changed_fields(before: ProductState, after: ProductState) -> tuple[str, ...]:
    changed: list[str] = []
    for field in _FIELD_ORDER:
        if getattr(before, field) == getattr(after, field):
            continue
        identity_changed = (
            before.title != after.title or before.price_label != after.price_label
        )
        if (
            field == "product_card_json"
            and identity_changed
            and _card_without_derived_identity(before.product_card_json)
            == _card_without_derived_identity(after.product_card_json)
        ):
            continue
        changed.append(field)
    return tuple(changed)


def _card_without_derived_identity(value: str) -> str:
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return value
    if not isinstance(payload, dict):
        return value
    normalized = dict(payload)
    data_map = normalized.get("dataMap")
    if isinstance(data_map, dict):
        normalized_data_map = dict(data_map)
        normalized_data_map.pop("title", None)
        normalized_data_map.pop("price", None)
        normalized["dataMap"] = normalized_data_map
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _unchanged(state: ProductState) -> ProductChange:
    return ProductChange(
        action="unchanged",
        uid=state.uid,
        before=state,
        after=state,
        changed_fields=(),
    )


def _duplicate_uids(values: Any) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _require_identity(
    *, field: str, project_value: str, snapshot_value: Any
) -> None:
    normalized_snapshot_value = _required_text(
        snapshot_value, field=f"snapshot.{field}"
    )
    if project_value != normalized_snapshot_value:
        raise MasterSnapshotPlanError(
            "project_identity_mismatch",
            "本地项目与 Master 快照身份不一致。",
            details={
                "field": field,
                "project_value": project_value,
                "snapshot_value": normalized_snapshot_value,
            },
        )


def _project_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _invalid_projection("project.id")
    return value


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid_projection(field)
    return value


def _flag(value: Any, *, field: str) -> int:
    normalized = _integer(value, field=field)
    if normalized not in {0, 1}:
        raise _invalid_projection(field)
    return normalized


def _required_text(value: Any, *, field: str) -> str:
    text = _plain_text(value, field=field)
    if not text:
        raise _invalid_projection(field)
    return text


def _optional_text(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field=field)


def _plain_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid_projection(field)
    return value.strip()


def _invalid_projection(field: str) -> MasterSnapshotPlanError:
    return MasterSnapshotPlanError(
        "invalid_snapshot_projection",
        "同步输入缺少必要字段。",
        details={"field": field},
    )
