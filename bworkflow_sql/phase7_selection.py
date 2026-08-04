from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .artifact_approvals import atomic_update_pipeline
from .utils import safe_text


PHASE7_OUTPUT_BRANCHES = {"final_mp4"}
PHASE7_PRODUCT_MEDIA_MODES = {"video_preferred"}
PHASE7_ORDER_STRATEGIES = {"price_segment_shuffle", "stable"}
PHASE7_SEQUENCE_MODES = {"standard", "top"}
PHASE7_SELECTION_SOURCE = "explicit_user_confirmation"


class Phase7SelectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_phase7_selection(
    *,
    output_branch: str,
    account: str,
    product_card_template_id: str,
    product_media_mode: str,
    product_order_strategy: str,
    mode: str,
    top_uids: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    branch = safe_text(output_branch)
    account_label = safe_text(account)
    template_id = safe_text(product_card_template_id)
    media_mode = safe_text(product_media_mode)
    order_strategy = safe_text(product_order_strategy)
    sequence_mode = safe_text(mode)
    uid_list = (
        [safe_text(uid) for uid in top_uids.split(",")]
        if isinstance(top_uids, str)
        else [safe_text(uid) for uid in top_uids or []]
    )
    uid_list = [uid for uid in uid_list if uid]

    if branch not in PHASE7_OUTPUT_BRANCHES:
        raise Phase7SelectionError("phase7_selection_invalid", f"unsupported output_branch: {branch or '<empty>'}")
    if not account_label:
        raise Phase7SelectionError("phase7_selection_invalid", "phase 7 account is required")
    if not template_id:
        raise Phase7SelectionError("phase7_selection_invalid", "phase 7 product-card template is required")
    if media_mode not in PHASE7_PRODUCT_MEDIA_MODES:
        raise Phase7SelectionError(
            "phase7_selection_invalid",
            f"unsupported product_media_mode: {media_mode or '<empty>'}",
        )
    if order_strategy not in PHASE7_ORDER_STRATEGIES:
        raise Phase7SelectionError(
            "phase7_selection_invalid",
            f"unsupported product_order_strategy: {order_strategy or '<empty>'}",
        )
    if sequence_mode not in PHASE7_SEQUENCE_MODES:
        raise Phase7SelectionError("phase7_selection_invalid", f"unsupported mode: {sequence_mode or '<empty>'}")
    if sequence_mode == "top" and not uid_list:
        raise Phase7SelectionError("phase7_selection_invalid", "top mode requires at least one top UID")
    if sequence_mode == "standard" and uid_list:
        raise Phase7SelectionError("phase7_selection_invalid", "standard mode cannot carry top UIDs")

    return {
        "output_branch": branch,
        "account": account_label,
        "product_card_template_id": template_id,
        "product_media_mode": media_mode,
        "product_order_strategy": order_strategy,
        "mode": sequence_mode,
        "top_uids": uid_list,
    }


def phase7_selection_hash(selection: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(selection), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def phase7_confirmation_hash(selection: Mapping[str, Any], source_snapshot: Mapping[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"selection": dict(selection)}
    if source_snapshot is not None:
        payload["source_snapshot"] = dict(source_snapshot)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def confirm_phase7_selection(
    pipeline_path: str | Path,
    *,
    output_branch: str,
    account: str,
    product_card_template_id: str,
    product_media_mode: str,
    product_order_strategy: str,
    mode: str,
    top_uids: str | list[str] | tuple[str, ...] | None = None,
    source_snapshot: Mapping[str, Any] | None = None,
    confirmed_at: str = "",
) -> dict[str, Any]:
    selection = normalize_phase7_selection(
        output_branch=output_branch,
        account=account,
        product_card_template_id=product_card_template_id,
        product_media_mode=product_media_mode,
        product_order_strategy=product_order_strategy,
        mode=mode,
        top_uids=top_uids,
    )
    normalized_source = _normalize_source_snapshot(source_snapshot, selection=selection)
    selection_digest = phase7_confirmation_hash(selection, normalized_source)
    timestamp = safe_text(confirmed_at) or datetime.now().astimezone().isoformat(timespec="seconds")

    def update(payload: dict[str, Any]) -> None:
        phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
        assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
        existing_order_lock = (
            assembly.get("product_order_lock")
            if isinstance(assembly.get("product_order_lock"), dict)
            else None
        )
        if existing_order_lock is not None and normalized_source is not None:
            locked_uids = {
                safe_text(uid).casefold()
                for uid in existing_order_lock.get("product_uids") or []
                if safe_text(uid)
            }
            confirmed_uids = {
                safe_text(uid).casefold()
                for uid in normalized_source.get("product_uids") or []
                if safe_text(uid)
            }
            if locked_uids != confirmed_uids:
                assembly.pop("product_order_lock", None)
        assembly.update(selection)
        assembly.pop("generate_jianying_draft", None)
        assembly["selection_confirmation"] = {
            "status": "confirmed",
            "source": PHASE7_SELECTION_SOURCE,
            "confirmed_at": timestamp,
            "selection_hash": selection_digest,
            "selection": selection,
        }
        if normalized_source is not None:
            assembly["selection_confirmation"]["source_snapshot"] = normalized_source
        phases["assembly"] = assembly
        payload["phases"] = phases
        payload["account"] = selection["account"]
        payload["updated_at"] = timestamp

    payload = atomic_update_pipeline(pipeline_path, update)
    return {
        "ok": True,
        "status": "confirmed",
        "pipeline_path": str(Path(pipeline_path).expanduser().resolve()),
        "selection": selection,
        "selection_hash": selection_digest,
        "confirmation": payload["phases"]["assembly"]["selection_confirmation"],
    }


def validated_phase7_selection(
    pipeline_path: str | Path,
    *,
    required_output: str,
    account: str,
    product_card_template_id: str,
    product_media_mode: str,
    product_order_strategy: str,
    mode: str,
    top_uids: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    pipeline = Path(pipeline_path).expanduser().resolve()
    if not pipeline.is_file():
        raise Phase7SelectionError("phase7_selection_unconfirmed", f"pipeline does not exist: {pipeline}")
    payload = json.loads(pipeline.read_text(encoding="utf-8-sig"))
    phases = payload.get("phases") if isinstance(payload, dict) and isinstance(payload.get("phases"), dict) else {}
    assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
    confirmation = assembly.get("selection_confirmation") if isinstance(assembly.get("selection_confirmation"), dict) else {}
    stored_selection = confirmation.get("selection") if isinstance(confirmation.get("selection"), dict) else {}
    source_snapshot = confirmation.get("source_snapshot") if isinstance(confirmation.get("source_snapshot"), dict) else None
    if (
        confirmation.get("status") != "confirmed"
        or confirmation.get("source") != PHASE7_SELECTION_SOURCE
        or not stored_selection
    ):
        raise Phase7SelectionError(
            "phase7_selection_unconfirmed",
            "phase 7 settings must be explicitly confirmed before formal rendering",
        )
    stored_hash = safe_text(confirmation.get("selection_hash"))
    requires_live_source = _pipeline_schema_version(payload) >= 3
    if requires_live_source and source_snapshot is None:
        raise Phase7SelectionError(
            "phase7_selection_invalid",
            "episode phase 7 selection is missing its confirmed Master live-source snapshot",
        )
    if not stored_hash or stored_hash != phase7_confirmation_hash(stored_selection, source_snapshot):
        raise Phase7SelectionError(
            "phase7_selection_invalid",
            "phase 7 selection confirmation hash is missing or invalid",
        )

    expected = normalize_phase7_selection(
        output_branch=safe_text(stored_selection.get("output_branch")),
        account=account,
        product_card_template_id=product_card_template_id,
        product_media_mode=product_media_mode,
        product_order_strategy=product_order_strategy,
        mode=mode,
        top_uids=top_uids,
    )
    required = safe_text(required_output)
    allowed_outputs = {"final_mp4": {"final_mp4"}}
    if required not in allowed_outputs or safe_text(stored_selection.get("output_branch")) not in allowed_outputs[required]:
        raise Phase7SelectionError(
            "phase7_selection_mismatch",
            f"confirmed output branch does not include {required or '<empty>'}",
        )
    expected["output_branch"] = safe_text(stored_selection.get("output_branch"))
    if dict(stored_selection) != expected:
        mismatches = [key for key in expected if stored_selection.get(key) != expected.get(key)]
        raise Phase7SelectionError(
            "phase7_selection_mismatch",
            "render arguments do not match confirmed phase 7 settings: " + ", ".join(mismatches),
        )
    return {
        "selection": expected,
        "selection_hash": stored_hash,
        "source": PHASE7_SELECTION_SOURCE,
        "confirmed_at": safe_text(confirmation.get("confirmed_at")),
        "source_snapshot": source_snapshot,
    }


def _normalize_source_snapshot(
    source_snapshot: Mapping[str, Any] | None,
    *,
    selection: Mapping[str, Any],
) -> dict[str, Any] | None:
    if source_snapshot is None:
        return None
    status = safe_text(source_snapshot.get("status"))
    snapshot_id = safe_text(source_snapshot.get("master_snapshot_id"))
    product_uids = [safe_text(uid) for uid in source_snapshot.get("product_uids") or []]
    product_uids = sorted({uid for uid in product_uids if uid})
    if status != "ready" or not snapshot_id or not product_uids:
        raise Phase7SelectionError("phase7_selection_invalid", "phase 7 requires a verified Master live-source snapshot")
    invalid_top_uids = [uid for uid in selection.get("top_uids") or [] if uid not in product_uids]
    if invalid_top_uids:
        raise Phase7SelectionError(
            "phase7_selection_invalid",
            "top UIDs are absent from the confirmed Master source: " + ", ".join(invalid_top_uids),
        )
    featured_products = source_snapshot.get("featured_products")
    normalized_featured = [
        {"uid": safe_text(item.get("uid")), "title": safe_text(item.get("title"))}
        for item in featured_products or []
        if isinstance(item, Mapping) and safe_text(item.get("uid")) and safe_text(item.get("title"))
    ]
    return {
        "kind": "bworkflow.phase7_master_live_selection",
        "status": "ready",
        "master_snapshot_id": snapshot_id,
        "generated_at_utc": safe_text(source_snapshot.get("generated_at_utc")),
        "workspace_id": safe_text(source_snapshot.get("workspace_id")),
        "scheme_id": safe_text(source_snapshot.get("scheme_id")),
        "product_uids": product_uids,
        "featured_products": normalized_featured,
    }


def execution_contract_inputs(pipeline_path: str | Path) -> dict[str, Any]:
    """Read the already-confirmed Phase 7 scope; never infer it from project rows."""
    pipeline = Path(pipeline_path).expanduser().resolve()
    if not pipeline.is_file():
        raise Phase7SelectionError("phase7_selection_unconfirmed", f"pipeline does not exist: {pipeline}")
    payload = json.loads(pipeline.read_text(encoding="utf-8-sig"))
    phases = payload.get("phases") if isinstance(payload, dict) and isinstance(payload.get("phases"), dict) else {}
    assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
    confirmation = assembly.get("selection_confirmation") if isinstance(assembly.get("selection_confirmation"), dict) else {}
    selection = confirmation.get("selection") if isinstance(confirmation.get("selection"), dict) else {}
    source_snapshot = confirmation.get("source_snapshot") if isinstance(confirmation.get("source_snapshot"), dict) else None
    if confirmation.get("status") != "confirmed" or confirmation.get("source") != PHASE7_SELECTION_SOURCE:
        raise Phase7SelectionError("phase7_selection_unconfirmed", "formal assembly requires a confirmed phase 7 selection")
    normalized = normalize_phase7_selection(
        output_branch=safe_text(selection.get("output_branch")),
        account=safe_text(selection.get("account")),
        product_card_template_id=safe_text(selection.get("product_card_template_id")),
        product_media_mode=safe_text(selection.get("product_media_mode")),
        product_order_strategy=safe_text(selection.get("product_order_strategy")),
        mode=safe_text(selection.get("mode")),
        top_uids=selection.get("top_uids") or [],
    )
    if dict(selection) != normalized or safe_text(confirmation.get("selection_hash")) != phase7_confirmation_hash(normalized, source_snapshot):
        raise Phase7SelectionError("phase7_selection_invalid", "phase 7 selection confirmation hash is invalid")
    source = _normalize_source_snapshot(source_snapshot, selection=normalized)
    if source is None:
        raise Phase7SelectionError("phase7_selection_invalid", "formal assembly requires a confirmed Master source snapshot")
    episode_id = safe_text(payload.get("episode_id"))
    if not episode_id:
        raise Phase7SelectionError("episode_identity_missing", "formal assembly requires pipeline episode_id")
    try:
        project_id = int(payload.get("bworkflow_project_id") or 0)
    except (TypeError, ValueError):
        project_id = 0
    if project_id <= 0:
        raise Phase7SelectionError("episode_identity_missing", "formal assembly requires pipeline B-Workflow project identity")
    return {
        "episode_id": episode_id,
        "project_id": project_id,
        "selection": normalized,
        "selection_hash": safe_text(confirmation.get("selection_hash")),
        "source_snapshot": source,
        "product_uids": list(source["product_uids"]),
    }


def _pipeline_schema_version(payload: Mapping[str, Any]) -> int:
    try:
        return int(payload.get("schema_version") or 0)
    except (TypeError, ValueError):
        return 0
