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
    selection_digest = phase7_selection_hash(selection)
    timestamp = safe_text(confirmed_at) or datetime.now().astimezone().isoformat(timespec="seconds")

    def update(payload: dict[str, Any]) -> None:
        phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
        assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
        assembly.update(selection)
        assembly.pop("generate_jianying_draft", None)
        assembly["selection_confirmation"] = {
            "status": "confirmed",
            "source": PHASE7_SELECTION_SOURCE,
            "confirmed_at": timestamp,
            "selection_hash": selection_digest,
            "selection": selection,
        }
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
    if not stored_hash or stored_hash != phase7_selection_hash(stored_selection):
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
    }
