from __future__ import annotations

import json
from typing import Any

from .db import Database
from .utils import now_iso, safe_text


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def projects(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.fetchall("SELECT * FROM projects ORDER BY name COLLATE NOCASE ASC, id ASC")]

    def project(self, project_id: int) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
        return dict(row) if row else None

    def products(self, project_id: int, *, include_removed: bool = True) -> list[dict[str, Any]]:
        where = "" if include_removed else "AND removed_from_master=0 AND active=1"
        return [
            dict(row)
            for row in self.db.fetchall(
                f"SELECT * FROM products WHERE project_id=? {where} ORDER BY sort_order, id",
                (project_id,),
            )
        ]

    def script_blocks(self, project_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.fetchall(
                """
                SELECT * FROM script_blocks
                WHERE project_id=? AND active=1
                ORDER BY CASE script_type WHEN 'intro' THEN 1 WHEN 'product' THEN 2 ELSE 3 END, owner_uid, price_range_label, block_label
                """,
                (project_id,),
            )
        ]

    def asset_bindings(self, project_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.fetchall(
                "SELECT * FROM asset_bindings WHERE project_id=? ORDER BY uid, asset_type, account_label, block_label, path",
                (project_id,),
            )
        ]

    def accounts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.fetchall("SELECT * FROM accounts ORDER BY enabled DESC, label")]

    def upsert_products_from_master(self, project_id: int, products: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        existing = {item["uid"]: item for item in self.products(project_id)}
        incoming = {safe_text(item.get("uid")): item for item in products if safe_text(item.get("uid"))}
        ts = now_iso()
        added: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        with self.db.connect() as conn:
            for index, (uid, item) in enumerate(incoming.items(), start=1):
                title = safe_text(item.get("title") or item.get("product_name"))
                price_label = safe_text(item.get("price_label") or item.get("price"))
                master_item_id = safe_text(item.get("master_item_id") or item.get("id"))
                product_card_json = _product_card_json(item, title=title, price_label=price_label)
                if uid in existing:
                    old = existing[uid]
                    changed = (
                        old["title"] != title
                        or old["price_label"] != price_label
                        or safe_text(old.get("product_card_json")) != product_card_json
                        or int(old["removed_from_master"]) != 0
                    )
                    conn.execute(
                        """
                        UPDATE products
                        SET title=?, price_label=?, sort_order=?, master_item_id=?, product_card_json=?, active=1, removed_from_master=0, updated_at=?
                        WHERE project_id=? AND uid=?
                        """,
                        (title, price_label, index, master_item_id, product_card_json, ts, project_id, uid),
                    )
                    if changed:
                        updated.append({"uid": uid, "title": title, "price_label": price_label})
                else:
                    conn.execute(
                        """
                        INSERT INTO products (project_id, uid, title, price_label, sort_order, master_item_id, product_card_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (project_id, uid, title, price_label, index, master_item_id, product_card_json, ts, ts),
                    )
                    added.append({"uid": uid, "title": title, "price_label": price_label})
            for uid, old in existing.items():
                if uid not in incoming and not int(old["removed_from_master"]):
                    conn.execute(
                        "UPDATE products SET removed_from_master=1, active=0, updated_at=? WHERE project_id=? AND uid=?",
                        (ts, project_id, uid),
                    )
                    removed.append(dict(old))
        return {"added": added, "updated": updated, "removed": removed}


def _product_card_json(item: dict[str, Any], *, title: str, price_label: str) -> str:
    payload = _product_card_payload(item, title=title, price_label=price_label)
    if not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _product_card_payload(item: dict[str, Any], *, title: str, price_label: str) -> dict[str, Any]:
    spec = item.get("spec")
    slots = _param_slots(spec if isinstance(spec, dict) else {})
    cover = _first_text(
        item,
        "cover",
        "cover_url",
        "coverUrl",
        "image",
        "image_url",
        "imageUrl",
        "main_image_url",
        "thumbnail_url",
    )
    remark = _first_text(item, "remark", "summary", "evaluation", "comment")
    template_id = _first_text(item, "product_card_template_id", "template_id", "templateId")

    if not any([cover, remark, slots, template_id]):
        return {}

    data_map: dict[str, str] = {
        "title": title,
        "price": price_label,
    }
    if cover:
        data_map["cover"] = cover
    if remark:
        data_map["remark"] = remark

    payload: dict[str, Any] = {
        "dataMap": data_map,
        "slots": slots,
    }
    if template_id:
        payload["templateId"] = template_id
    if cover:
        payload["coverAsset"] = cover
    return payload


def _param_slots(spec: dict[str, Any]) -> list[dict[str, str]]:
    slots: list[dict[str, str]] = []
    for key, value in spec.items():
        label = safe_text(key)
        if not label or label.startswith("_"):
            continue
        text = _stringify_value(value)
        if not text:
            continue
        slots.append({"label": label, "value": text})
    return slots


def _stringify_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return safe_text(value)


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = safe_text(item.get(key))
        if text:
            return text
    return ""
