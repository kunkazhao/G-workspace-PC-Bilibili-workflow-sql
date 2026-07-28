from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .master_contracts import (
    MasterCategoryIdentity,
    MasterMoney,
    MasterPriceRange,
    MasterProductCard,
    MasterSchemeIdentity,
    MasterSchemeSnapshot,
    MasterSnapshotProduct,
    MasterSpecSlot,
    MasterWorkspace,
)
from .master_snapshot_sync import ProductState
from .utils import safe_text


def build_episode_source_payload(
    snapshot: MasterSchemeSnapshot,
    records: tuple[ProductState, ...],
) -> tuple[dict[str, Any], str, str]:
    """Return a canonical, self-contained projection that downstream stages can replay."""
    payload = {
        "kind": "bworkflow.episode_source_snapshot",
        "schema_version": 1,
        "master_snapshot": _snapshot_payload(snapshot),
        "product_projection": [
            {
                "id": 0,
                "project_id": item.project_id,
                "uid": item.uid,
                "title": item.title,
                "price_label": item.price_label,
                "sort_order": item.sort_order,
                "master_item_id": item.master_item_id,
                "product_card_json": item.product_card_json,
                "active": item.active,
                "removed_from_master": item.removed_from_master,
            }
            for item in records
        ],
    }
    source_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_sha256 = "sha256:" + hashlib.sha256(source_json.encode("utf-8")).hexdigest()
    return payload, source_json, source_sha256


def snapshot_from_episode_source(payload: Mapping[str, Any]) -> MasterSchemeSnapshot:
    raw = payload.get("master_snapshot")
    if not isinstance(raw, Mapping):
        raise ValueError("episode source snapshot lacks master_snapshot")
    workspace = _mapping(raw.get("workspace"), "workspace")
    scheme = _mapping(raw.get("scheme"), "scheme")
    category = _mapping(scheme.get("category"), "category")
    price_ranges = raw.get("price_ranges")
    products = raw.get("products")
    if not isinstance(price_ranges, list) or not isinstance(products, list):
        raise ValueError("episode source snapshot master payload is invalid")
    return MasterSchemeSnapshot(
        schema_version=_text(raw.get("schema_version")),
        generated_at_utc=_text(raw.get("generated_at_utc")),
        snapshot_id=_text(raw.get("snapshot_id")),
        workspace=MasterWorkspace(
            id=_text(workspace.get("id")),
            name=_text(workspace.get("name")),
            slug=_optional_text(workspace.get("slug")),
        ),
        scheme=MasterSchemeIdentity(
            id=_text(scheme.get("id")),
            name=_text(scheme.get("name")),
            category=MasterCategoryIdentity(
                id=_text(category.get("id")), name=_text(category.get("name"))
            ),
            updated_at=_optional_text(scheme.get("updated_at")),
        ),
        price_ranges=tuple(
            MasterPriceRange(
                min_amount=_optional_text(_mapping(item, "price_range").get("min_amount")),
                max_amount=_optional_text(_mapping(item, "price_range").get("max_amount")),
                label=_text(_mapping(item, "price_range").get("label")),
            )
            for item in price_ranges
        ),
        products=tuple(_product_from_payload(_mapping(item, "product")) for item in products),
    )


def source_payload_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(_text(row.get("source_json")))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("episode source snapshot is invalid") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "bworkflow.episode_source_snapshot":
        raise ValueError("episode source snapshot has an unsupported contract")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual != _text(row.get("source_sha256")):
        raise ValueError("episode source snapshot checksum mismatch")
    return payload


def _snapshot_payload(snapshot: MasterSchemeSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "generated_at_utc": snapshot.generated_at_utc,
        "snapshot_id": snapshot.snapshot_id,
        "workspace": {"id": snapshot.workspace.id, "name": snapshot.workspace.name, "slug": snapshot.workspace.slug},
        "scheme": {
            "id": snapshot.scheme.id,
            "name": snapshot.scheme.name,
            "category": {"id": snapshot.scheme.category.id, "name": snapshot.scheme.category.name},
            "updated_at": snapshot.scheme.updated_at,
        },
        "price_ranges": [
            {"min_amount": item.min_amount, "max_amount": item.max_amount, "label": item.label}
            for item in snapshot.price_ranges
        ],
        "products": [
            {
                "master_item_id": item.master_item_id,
                "uid": item.uid,
                "title": item.title,
                "sort_order": item.sort_order,
                "price": {"amount": item.price.amount, "currency": item.price.currency, "source": item.price.source, "display": item.price.display},
                "card": {
                    "cover_url": item.card.cover_url,
                    "remark": item.card.remark,
                    "spec_slots": [{"label": slot.label, "value": slot.value} for slot in item.card.spec_slots],
                    "template_id": item.card.template_id,
                },
                "tags": list(item.tags),
                "featured": item.featured,
                "source_updated_at": item.source_updated_at,
            }
            for item in snapshot.products
        ],
    }


def _product_from_payload(item: Mapping[str, Any]) -> MasterSnapshotProduct:
    price = _mapping(item.get("price"), "price")
    card = _mapping(item.get("card"), "card")
    slots = card.get("spec_slots")
    tags = item.get("tags")
    if not isinstance(slots, list) or not isinstance(tags, list):
        raise ValueError("episode source snapshot product is invalid")
    return MasterSnapshotProduct(
        master_item_id=_text(item.get("master_item_id")), uid=_text(item.get("uid")),
        title=_text(item.get("title")), sort_order=_integer(item.get("sort_order")),
        price=MasterMoney(amount=_optional_text(price.get("amount")), currency=_text(price.get("currency")), source=_text(price.get("source")), display=_text(price.get("display"))),
        card=MasterProductCard(
            cover_url=_optional_text(card.get("cover_url")), remark=_optional_text(card.get("remark")),
            spec_slots=tuple(MasterSpecSlot(label=_text(_mapping(slot, "spec_slot").get("label")), value=_text(_mapping(slot, "spec_slot").get("value"))) for slot in slots),
            template_id=_optional_text(card.get("template_id")),
        ),
        tags=tuple(_text(tag) for tag in tags), featured=bool(item.get("featured")),
        source_updated_at=_optional_text(item.get("source_updated_at")),
    )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"episode source snapshot {label} is invalid")
    return value


def _text(value: Any) -> str:
    result = safe_text(value)
    if not result:
        raise ValueError("episode source snapshot has a required blank field")
    return result


def _optional_text(value: Any) -> str | None:
    return safe_text(value) or None


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("episode source snapshot has an invalid integer")
    return value
