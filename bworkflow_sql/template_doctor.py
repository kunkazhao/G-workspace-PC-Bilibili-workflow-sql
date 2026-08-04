from __future__ import annotations

from typing import Any

from .db import Database
from .media_readiness import audit_product_video_media
from .render_package_builder import (
    DEFAULT_PRODUCT_MEDIA_MODE,
    SUPPORTED_PRODUCT_MEDIA_MODES,
    product_card_payload_for_product,
)
from .repositories import Repository
from .template_config import (
    display_video_slot_for_product_card_template_id,
    product_card_text_capacity_certification_issues,
    resolve_product_card_template,
)
from .utils import safe_text


REQUIRED_TEMPLATE_METADATA_KEYS = (
    "sourceCanvas",
    "outputCanvas",
    "cardPlacement",
    "coverMediaSlot",
    "slotDeclarations",
)


def diagnose_template_flow(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    product_card_template_id: str = "",
    product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
    products: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    account = safe_text(account_label)
    requested_template_id = safe_text(product_card_template_id)
    media_mode = safe_text(product_media_mode) or DEFAULT_PRODUCT_MEDIA_MODE
    if media_mode not in SUPPORTED_PRODUCT_MEDIA_MODES:
        raise ValueError(f"unsupported product_media_mode: {media_mode}")

    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    issues: list[dict[str, Any]] = []
    if not requested_template_id:
        issues.append(
            {
                "level": "error",
                "code": "product_card_template_required",
                "message": "product-card template must be explicitly confirmed for still/product-image or phase-7 output.",
            }
        )
        return _payload(
            project_id=project_id,
            account=account,
            media_mode=media_mode,
            template={},
            products_checked=0,
            media_inventory=_media_inventory([], []),
            issues=issues,
            next_hint={
                "action": "confirm_product_card_template",
                "command": "",
            },
        )

    selected_template = resolve_product_card_template(
        account,
        requested_template_id,
        require_explicit=True,
    )
    template_id = safe_text(selected_template.get("templateId")) or requested_template_id
    template = {
        "id": template_id,
        "displayName": safe_text(selected_template.get("displayName")),
        "version": safe_text(selected_template.get("templateVersion")),
        "confirmed": True,
        "selectionSource": "explicit",
    }
    issues.extend(_template_metadata_issues(selected_template))
    try:
        display_video_slot_for_product_card_template_id(template_id)
    except ValueError as exc:
        issues.append(
            {
                "level": "error",
                "code": "display_video_slot_unavailable",
                "template_id": template_id,
                "message": str(exc),
            }
        )

    assets = repo.asset_bindings(project_id)
    active_products = products if products is not None else repo.products(project_id, include_removed=False)
    media_readiness = audit_product_video_media(
        active_products,
        assets,
        video_root=safe_text(project.get("video_root")),
        project=project,
    )
    selected_video_paths = media_readiness.get("selected_paths") or {}
    product_count = 0
    video_ready_items: list[dict[str, Any]] = []
    video_missing_items: list[dict[str, Any]] = []
    for product in active_products:
        product_count += 1
        uid = safe_text(product.get("uid"))
        title = safe_text(product.get("title"))
        video_path = safe_text(selected_video_paths.get(uid))
        if video_path:
            video_ready_items.append({"uid": uid, "title": title, "path": video_path})
        else:
            video_missing_items.append({"uid": uid, "title": title})
        product_card = product_card_payload_for_product(
            product,
            project=project,
            # Formal Remotion segments resolve their own structured cover/video
            # media. Historical whole-card PNG bindings are never render input.
            fallback_image_path=None,
            account_label=account,
            product_card_template_id=template_id,
        )
        if not product_card:
            issues.append(
                {
                    "level": "error",
                    "code": "missing_product_card_payload",
                    "uid": uid,
                    "message": "product cannot produce a productCard payload for the selected template.",
                }
            )
            continue
        if media_mode == "video_preferred" and "coverMediaSlot" not in product_card:
            issues.append(
                {
                    "level": "error",
                    "code": "missing_video_slot",
                    "uid": uid,
                    "message": "video_preferred output requires productCard.coverMediaSlot or a derived displayVideoSlot.",
                }
            )
    return _payload(
        project_id=project_id,
        account=account,
        media_mode=media_mode,
        template=template,
        products_checked=product_count,
        media_inventory=_media_inventory(video_ready_items, video_missing_items, readiness=media_readiness),
        issues=issues,
        next_hint=_next_hint(project_id, account, template_id, issues),
    )


def _payload(
    *,
    project_id: int,
    account: str,
    media_mode: str,
    template: dict[str, Any],
    products_checked: int,
    media_inventory: dict[str, Any],
    issues: list[dict[str, Any]],
    next_hint: dict[str, Any],
) -> dict[str, Any]:
    errors = sum(1 for issue in issues if issue.get("level") == "error")
    warnings = sum(1 for issue in issues if issue.get("level") == "warning")
    return {
        "ok": not issues,
        "status": "ok" if not issues else "issues_found",
        "project_id": project_id,
        "account": account,
        "product_media_mode": media_mode,
        "template": template,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "products_checked": products_checked,
        },
        "media_inventory": media_inventory,
        "issues": issues,
        "next": next_hint,
    }


def _template_metadata_issues(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    template_id = safe_text(metadata.get("templateId"))
    for key in REQUIRED_TEMPLATE_METADATA_KEYS:
        value = metadata.get(key)
        if key == "slotDeclarations":
            ok = isinstance(value, list) and bool(value)
        else:
            ok = isinstance(value, dict) and bool(value)
        if not ok:
            issues.append(
                {
                    "level": "error",
                    "code": "template_metadata_incomplete",
                    "template_id": template_id,
                    "field": key,
                    "message": f"Remotion template metadata is missing or invalid: {key}",
                }
            )
    issues.extend(product_card_text_capacity_certification_issues(metadata))
    return issues


def _next_hint(
    project_id: int,
    account: str,
    template_id: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    codes = {safe_text(issue.get("code")) for issue in issues}
    if not issues:
        return {
            "action": "continue_phase_7",
            "command": f"python -m bworkflow_sql render-package {project_id} --account {account} --product-card-template-id {template_id}",
        }
    if codes.intersection(
        {
            "text_capacity_uncertified",
            "text_capacity_template_version_mismatch",
            "text_capacity_component_source_missing",
            "text_capacity_source_hash_mismatch",
            "text_capacity_supporting_source_invalid",
            "text_capacity_supporting_source_missing",
            "text_capacity_supporting_source_hash_mismatch",
            "text_capacity_baseline_missing",
            "text_capacity_baseline_hash_mismatch",
            "text_capacity_baseline_version_mismatch",
        }
    ):
        return {
            "action": "run_product_card_text_capacity_gate",
            "command": (
                "node remotion-renderer/scripts/audit-product-card-text-capacity.mjs "
                f"&& python -m bworkflow_sql template-doctor {project_id} --account {account} "
                f"--product-card-template-id {template_id}"
            ),
        }
    if codes.intersection({"missing_video_slot", "display_video_slot_unavailable"}):
        return {
            "action": "fix_template_metadata_then_recheck",
            "command": f"python -m bworkflow_sql template-doctor {project_id} --account {account} --product-card-template-id {template_id}",
        }
    return {
        "action": "inspect_issues",
        "command": f"python -m bworkflow_sql template-doctor {project_id} --account {account} --product-card-template-id {template_id}",
    }


def _ready_video_asset(assets: list[dict[str, Any]], *, uid: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if safe_text(asset.get("asset_type")) != "video":
            continue
        if safe_text(asset.get("status")) != "ready" or safe_text(asset.get("uid")) != uid:
            continue
        path = Path(safe_text(asset.get("path")))
        if path.is_file():
            candidates.append(asset)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: safe_text(item.get("path")))[0]


def _media_inventory(
    video_ready_items: list[dict[str, Any]],
    video_missing_items: list[dict[str, Any]],
    *,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "total_products": len(video_ready_items) + len(video_missing_items),
        "video_ready": len(video_ready_items),
        "video_missing": len(video_missing_items),
        "video_items": video_ready_items,
        "missing_video_items": video_missing_items,
        "mode_explanation": {
            "cover_only": "all products use their current product cover inside the dynamic card",
            "video_preferred": "ready product videos are used; missing videos fall back to the current product cover",
        },
        "readiness": readiness or {},
    }
