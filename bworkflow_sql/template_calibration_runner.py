from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .settings import APP_ROOT
from .template_config import (
    display_template_for_product_card_template_id,
    image_set_for_template,
)
from .utils import safe_text


DEFAULT_CALIBRATION_TARGETS_PATH = APP_ROOT / "config" / "template-calibration-targets.json"
IMAGE_ISSUE_CODES = {
    "missing_ready_image_binding",
    "wrong_template_binding",
    "unknown_legacy_image_hash",
    "stale_product_image",
}
REQUIRED_TARGET_FIELDS = ("id", "project_id", "account", "template_id", "product_uid")


def load_template_calibration_targets(
    config_path: str | Path = DEFAULT_CALIBRATION_TARGETS_PATH,
    *,
    target_id: str = "",
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    path = Path(config_path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list):
        raise ValueError(f"template calibration config must contain targets[]: {path}")

    selected: list[dict[str, Any]] = []
    requested = safe_text(target_id)
    for index, item in enumerate(raw_targets, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"template calibration target #{index} must be an object")
        target = _normalize_target(item, index=index)
        if requested and target["id"] != requested:
            continue
        if not include_inactive and not target.get("active", True):
            continue
        selected.append(target)

    if requested and not selected:
        raise ValueError(f"template calibration target not found or inactive: {requested}")
    return selected


def run_template_calibration_targets(
    workflow: Any,
    *,
    targets: list[dict[str, Any]],
    regenerate_images: bool = True,
    dry_run: bool = False,
    draft_suffix: str = "",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for target in targets:
        rows.append(
            _run_one_target(
                workflow,
                target=target,
                regenerate_images=regenerate_images,
                dry_run=dry_run,
                draft_suffix=draft_suffix,
            )
        )
    failed = sum(1 for row in rows if not row.get("ok"))
    return {
        "ok": failed == 0,
        "summary": {
            "total": len(rows),
            "succeeded": len(rows) - failed,
            "failed": failed,
        },
        "targets": rows,
    }


def validate_probe_manifest(
    manifest_path: str | Path,
    *,
    expected_template_id: str,
    expected_display_template: str = "",
    expected_image_set: str = "",
) -> dict[str, Any]:
    path = Path(manifest_path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    product_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and safe_text(entry.get("type")) == "product"
    ]
    issues: list[dict[str, str]] = []
    if len(product_entries) != 1:
        issues.append({"code": "product_entry_count", "message": f"expected 1 product entry, got {len(product_entries)}"})
        return {"ok": False, "manifest_path": str(path), "issues": issues}

    entry = product_entries[0]
    display_template = safe_text(payload.get("display_template"))
    if expected_display_template and display_template != expected_display_template:
        issues.append(
            {
                "code": "display_template_mismatch",
                "message": f"expected {expected_display_template}, got {display_template}",
            }
        )

    image_path = safe_text(entry.get("image_path"))
    if expected_image_set and expected_image_set not in Path(image_path).parts:
        issues.append(
            {
                "code": "image_template_mismatch",
                "message": f"image path does not include {expected_image_set}: {image_path}",
            }
        )

    slot = entry.get("display_video_slot")
    slot_template_id = safe_text(slot.get("templateId")) if isinstance(slot, dict) else ""
    if slot_template_id != expected_template_id:
        issues.append(
            {
                "code": "slot_template_mismatch",
                "message": f"expected {expected_template_id}, got {slot_template_id}",
            }
        )

    return {
        "ok": not issues,
        "manifest_path": str(path),
        "display_template": display_template,
        "image_path": image_path,
        "slot_template_id": slot_template_id,
        "issues": issues,
    }


def _normalize_target(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    missing = [field for field in REQUIRED_TARGET_FIELDS if not safe_text(item.get(field))]
    if missing:
        raise ValueError(f"template calibration target #{index} missing required field: {missing[0]}")
    template_id = safe_text(item.get("template_id"))
    display_template = safe_text(item.get("display_template")) or display_template_for_product_card_template_id(template_id)
    image_set = safe_text(item.get("image_set")) or image_set_for_template(display_template)
    draft_name = safe_text(item.get("draft_name")) or (
        f"模板校准-{safe_text(item.get('account'))}-{safe_text(item.get('product_uid'))}"
    )
    return {
        "id": safe_text(item.get("id")),
        "project_id": int(item.get("project_id") or 0),
        "account": safe_text(item.get("account")),
        "template_id": template_id,
        "display_template": display_template,
        "image_set": image_set,
        "product_uid": safe_text(item.get("product_uid")),
        "draft_name": draft_name,
        "active": bool(item.get("active", True)),
        "notes": safe_text(item.get("notes")),
    }


def _run_one_target(
    workflow: Any,
    *,
    target: dict[str, Any],
    regenerate_images: bool,
    dry_run: bool,
    draft_suffix: str,
) -> dict[str, Any]:
    base = {
        "id": target["id"],
        "project_id": target["project_id"],
        "account": target["account"],
        "template_id": target["template_id"],
        "product_uid": target["product_uid"],
    }
    doctor = workflow.template_doctor(
        project_id=target["project_id"],
        account_label=target["account"],
        product_card_template_id=target["template_id"],
        product_media_mode="video_preferred",
    )
    image_regeneration: dict[str, Any] | None = None
    if not doctor.get("ok") and _has_image_issues(doctor):
        if not regenerate_images:
            return {
                **base,
                "ok": False,
                "status": "blocked_by_template_doctor",
                "doctor": doctor,
                "next": doctor.get("next"),
            }
        if dry_run:
            return {
                **base,
                "ok": True,
                "status": "dry_run_would_regenerate_images",
                "doctor": doctor,
                "next": doctor.get("next"),
            }
        image_regeneration = workflow.regenerate_product_card_images(
            project_id=target["project_id"],
            account_label=target["account"],
            mode="stale",
            product_uid="",
            product_card_template_id=target["template_id"],
        )
        if not image_regeneration.get("ok"):
            return {
                **base,
                "ok": False,
                "status": "product_images_failed",
                "doctor": doctor,
                "image_regeneration": image_regeneration,
            }
        doctor = workflow.template_doctor(
            project_id=target["project_id"],
            account_label=target["account"],
            product_card_template_id=target["template_id"],
            product_media_mode="video_preferred",
        )

    if not doctor.get("ok"):
        return {
            **base,
            "ok": False,
            "status": "blocked_by_template_doctor",
            "doctor": doctor,
            "next": doctor.get("next"),
            "image_regeneration": image_regeneration,
        }
    if dry_run:
        return {
            **base,
            "ok": True,
            "status": "dry_run_ready_to_calibrate",
            "doctor": doctor,
            "image_regeneration": image_regeneration,
        }

    draft_name = _draft_name_with_suffix(safe_text(target["draft_name"]), draft_suffix)
    calibration = workflow.template_calibration_probe(
        project_id=target["project_id"],
        account_label=target["account"],
        product_uid=target["product_uid"],
        draft_name=draft_name,
        draft_root=None,
        product_media_mode="video_preferred",
        product_card_template_id=target["template_id"],
    )
    manifest_check = None
    if calibration.get("ok") and safe_text(calibration.get("probe_manifest_path")):
        manifest_check = validate_probe_manifest(
            calibration["probe_manifest_path"],
            expected_template_id=target["template_id"],
            expected_display_template=safe_text(target.get("display_template")),
            expected_image_set=safe_text(target.get("image_set")),
        )
    ok = bool(calibration.get("ok")) and bool(manifest_check and manifest_check.get("ok"))
    return {
        **base,
        "ok": ok,
        "status": "draft_generated" if ok else "calibration_failed",
        "doctor": doctor,
        "image_regeneration": image_regeneration,
        "calibration": calibration,
        "manifest_check": manifest_check,
    }


def _has_image_issues(doctor: dict[str, Any]) -> bool:
    issues = doctor.get("issues") if isinstance(doctor.get("issues"), list) else []
    return any(safe_text(issue.get("code")) in IMAGE_ISSUE_CODES for issue in issues if isinstance(issue, dict))


def _draft_name_with_suffix(draft_name: str, suffix: str) -> str:
    clean_suffix = safe_text(suffix).strip("-_ ")
    if not clean_suffix:
        return draft_name
    if draft_name.endswith(f"-{clean_suffix}"):
        return draft_name
    return f"{draft_name}-{clean_suffix}"
