from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import Database
from .master_snapshot_sync import MasterSnapshotSyncPlan, ProductChange, ProductState
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

    def apply_master_snapshot_plan(
        self,
        plan: MasterSnapshotSyncPlan,
        *,
        applied_at: str | None = None,
    ) -> dict[str, Any]:
        ts = safe_text(applied_at) or now_iso()
        with self.db.connect() as conn:
            project = conn.execute(
                """
                SELECT id, workspace_id, category_id, scheme_id
                FROM projects
                WHERE id=?
                """,
                (plan.project_id,),
            ).fetchone()
            if project is None:
                raise ValueError(f"project not found: {plan.project_id}")
            expected_identity = {
                "workspace_id": plan.workspace_id,
                "category_id": plan.category_id,
                "scheme_id": plan.scheme_id,
            }
            for field, expected in expected_identity.items():
                if safe_text(project[field]) != expected:
                    raise ValueError(f"project identity changed before apply: {field}")

            for change in plan.changes:
                self._apply_snapshot_change(conn, change, ts)

            cursor = conn.execute(
                """
                UPDATE projects
                SET master_snapshot_id=?, master_snapshot_applied_at=?, updated_at=?
                WHERE id=?
                """,
                (plan.snapshot_id, ts, ts, plan.project_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("failed to persist Master snapshot provenance")

            event_id = self._insert_snapshot_event(conn, plan, ts)

        return {
            "snapshot_id": plan.snapshot_id,
            "applied_at": ts,
            "event_id": event_id,
            "change_count": plan.change_count,
            "unchanged_count": len(plan.unchanged),
            "added": [_change_summary(change) for change in plan.added],
            "updated": [_change_summary(change) for change in plan.updated],
            "removed": [_change_summary(change) for change in plan.removed],
            "reactivated": [
                _change_summary(change) for change in plan.reactivated
            ],
        }

    def _apply_snapshot_change(
        self,
        conn: sqlite3.Connection,
        change: ProductChange,
        applied_at: str,
    ) -> None:
        after = change.after
        if after is None:
            raise ValueError(f"snapshot change has no target state: {change.uid}")
        if change.action == "add":
            conn.execute(
                """
                INSERT INTO products (
                    project_id, uid, title, price_label, sort_order,
                    master_item_id, product_card_json, active,
                    removed_from_master, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _product_params(after) + (applied_at, applied_at),
            )
            return
        if change.action not in {"update", "remove", "reactivate"}:
            raise ValueError(f"unsupported snapshot change action: {change.action}")
        cursor = conn.execute(
            """
            UPDATE products
            SET title=?, price_label=?, sort_order=?, master_item_id=?,
                product_card_json=?, active=?, removed_from_master=?, updated_at=?
            WHERE project_id=? AND uid=?
            """,
            (
                after.title,
                after.price_label,
                after.sort_order,
                after.master_item_id,
                after.product_card_json,
                after.active,
                after.removed_from_master,
                applied_at,
                after.project_id,
                after.uid,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"snapshot product disappeared before apply: {after.uid}")

    def _insert_snapshot_event(
        self,
        conn: sqlite3.Connection,
        plan: MasterSnapshotSyncPlan,
        applied_at: str,
    ) -> int:
        message = (
            "Master 快照同步完成："
            f"新增 {len(plan.added)}，更新 {len(plan.updated)}，"
            f"恢复 {len(plan.reactivated)}，移除 {len(plan.removed)}；"
            f"snapshot {plan.snapshot_id}"
        )
        cursor = conn.execute(
            """
            INSERT INTO sync_events (
                project_id, event_type, status, message, created_at
            ) VALUES (?, 'master_snapshot_sync', 'success', ?, ?)
            """,
            (plan.project_id, message, applied_at),
        )
        event_id = int(cursor.lastrowid)
        status_by_action = {
            "add": "added",
            "update": "updated",
            "reactivate": "reactivated",
            "remove": "removed",
        }
        for change in plan.changes:
            state = change.after or change.before
            if state is None:
                raise ValueError(f"snapshot change has no evidence state: {change.uid}")
            changed_fields = ",".join(change.changed_fields)
            conn.execute(
                """
                INSERT INTO sync_event_items (
                    sync_event_id, item_kind, uid, title, status, message, path
                ) VALUES (?, 'product', ?, ?, ?, ?, '')
                """,
                (
                    event_id,
                    change.uid,
                    state.title,
                    status_by_action[change.action],
                    changed_fields,
                ),
            )
        return event_id


def _product_params(state: ProductState) -> tuple[Any, ...]:
    return (
        state.project_id,
        state.uid,
        state.title,
        state.price_label,
        state.sort_order,
        state.master_item_id,
        state.product_card_json,
        state.active,
        state.removed_from_master,
    )


def _change_summary(change: ProductChange) -> dict[str, Any]:
    state = change.after or change.before
    if state is None:
        raise ValueError(f"snapshot change has no summary state: {change.uid}")
    return {
        "uid": state.uid,
        "title": state.title,
        "price_label": state.price_label,
        "master_item_id": state.master_item_id,
        "sort_order": state.sort_order,
        "changed_fields": list(change.changed_fields),
    }


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
