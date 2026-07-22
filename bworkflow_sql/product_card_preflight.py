from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Database
from .product_image_modes import regeneration_mode_for_issue_codes
from .render_package_builder import product_card_content_fingerprint, product_card_payload_for_product
from .repositories import Repository
from .template_config import image_set_for_template, resolve_product_card_template
from .utils import safe_text


def product_card_preflight(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    product_card_template_id: str,
    product_uid: str = "",
    expect_cover: str = "",
) -> dict[str, Any]:
    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    account = safe_text(account_label)
    requested_template = safe_text(product_card_template_id)
    selected_template = resolve_product_card_template(account, requested_template, require_explicit=True)
    template_id = safe_text(selected_template.get("templateId")) or requested_template
    display_name = safe_text(selected_template.get("displayName"))
    expected_image_set = image_set_for_template(display_name)
    expected_cover = safe_text(expect_cover)
    selected_uid = safe_text(product_uid)

    products = [
        product
        for product in repo.products(project_id, include_removed=False)
        if not selected_uid or safe_text(product.get("uid")) == selected_uid
    ]
    assets = repo.asset_bindings(project_id)

    issues: list[dict[str, Any]] = []
    product_reports: list[dict[str, Any]] = []
    for product in products:
        uid = safe_text(product.get("uid"))
        raw_card = _load_product_card(product)
        data_map = raw_card.get("dataMap") if isinstance(raw_card.get("dataMap"), dict) else {}
        cover_value = safe_text(raw_card.get("coverAsset")) or safe_text(data_map.get("cover"))
        cover_match = _cover_matches(cover_value, expected_cover) if expected_cover else bool(cover_value)

        if not cover_value:
            issues.append(
                {
                    "level": "error",
                    "code": "missing_cover_asset",
                    "uid": uid,
                    "message": "local product_card_json has no coverAsset/dataMap.cover; run Master sync before product-images.",
                }
            )
        elif expected_cover and not cover_match:
            issues.append(
                {
                    "level": "error",
                    "code": "cover_asset_mismatch",
                    "uid": uid,
                    "cover": cover_value,
                    "expected_cover": expected_cover,
                    "message": "local product-card cover does not match the expected current cover.",
                }
            )

        payload = product_card_payload_for_product(
            product,
            project=project,
            fallback_image_path=None,
            account_label=account,
            product_card_template_id=template_id,
        )
        fingerprint = product_card_content_fingerprint(product, payload) if payload else ""
        image = _ready_image_asset(assets, uid=uid, account_label=account, image_set=expected_image_set)
        image_path = safe_text(image.get("path")) if image else ""
        image_ok = False
        if not image:
            issues.append(
                {
                    "level": "warning",
                    "code": "missing_ready_image_binding",
                    "uid": uid,
                    "message": "selected account/template has no ready product-card image yet.",
                }
            )
        else:
            stored_hash = safe_text(image.get("text_hash"))
            image_ok = bool(stored_hash and fingerprint and stored_hash == fingerprint)
            if not _image_path_uses_template(image_path, account_label=account, image_set=expected_image_set):
                issues.append(
                    {
                        "level": "error",
                        "code": "wrong_template_binding",
                        "uid": uid,
                        "path": image_path,
                        "expected_image_set": expected_image_set,
                        "message": "ready product-card image is not under the selected account/template folder.",
                    }
                )
            elif not image_ok:
                issues.append(
                    {
                        "level": "warning",
                        "code": "stale_or_unknown_image_fingerprint",
                        "uid": uid,
                        "path": image_path,
                        "stored_fingerprint": stored_hash,
                        "expected_fingerprint": fingerprint,
                        "message": "product-card image should be regenerated before draft or MP4 output.",
                    }
                )

        product_reports.append(
            {
                "uid": uid,
                "cover": cover_value,
                "cover_match": cover_match,
                "template_id": template_id,
                "image_path": image_path,
                "image_ok": image_ok,
            }
        )

    if selected_uid and not products:
        issues.append(
            {
                "level": "error",
                "code": "product_uid_not_found",
                "uid": selected_uid,
                "message": "selected product uid is not active in this project.",
            }
        )

    errors = sum(1 for issue in issues if issue.get("level") == "error")
    warnings = sum(1 for issue in issues if issue.get("level") == "warning")
    return {
        "ok": not issues,
        "status": "ok" if not issues else "blocked",
        "project_id": project_id,
        "account": account,
        "template": {"id": template_id, "displayName": display_name, "imageSet": expected_image_set},
        "summary": {"errors": errors, "warnings": warnings, "products_checked": len(products)},
        "issues": issues,
        "products": product_reports,
        "next": _next_hint(project_id, account, template_id, issues),
    }


def _load_product_card(product: dict[str, Any]) -> dict[str, Any]:
    raw = safe_text(product.get("product_card_json"))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _cover_matches(cover_value: str, expected_cover: str) -> bool:
    if not expected_cover:
        return bool(cover_value)
    cover = safe_text(cover_value)
    expected = safe_text(expected_cover)
    return cover == expected or Path(cover).name == expected or expected in cover


def _ready_image_asset(
    assets: list[dict[str, Any]],
    *,
    uid: str,
    account_label: str,
    image_set: str,
) -> dict[str, Any] | None:
    account = safe_text(account_label)
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if safe_text(asset.get("asset_type")) != "image":
            continue
        if safe_text(asset.get("status")) != "ready":
            continue
        if safe_text(asset.get("uid")) != uid:
            continue
        if safe_text(asset.get("account_label")) not in {account, ""}:
            continue
        path = safe_text(asset.get("path"))
        if not Path(path).is_file():
            continue
        candidates.append(asset)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            not _image_path_uses_template(
                safe_text(item.get("path")),
                account_label=account,
                image_set=image_set,
            ),
            safe_text(item.get("account_label")) != account,
            safe_text(item.get("path")),
        ),
    )[0]


def _image_path_uses_template(path_text: str, *, account_label: str, image_set: str) -> bool:
    path = safe_text(path_text)
    account = safe_text(account_label)
    image_set_value = safe_text(image_set)
    if not path or not account or not image_set_value:
        return False
    parts = {part for part in Path(path).parts}
    return account in parts and image_set_value in parts


def _next_hint(
    project_id: int,
    account: str,
    template_id: str,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    codes = {safe_text(issue.get("code")) for issue in issues}
    if not issues:
        return {
            "action": "run_product_images_or_continue",
            "command": (
                f"python -m bworkflow_sql product-images {project_id} "
                f"--account {account} --mode stale --product-card-template-id {template_id}"
            ),
        }
    if codes.intersection({"missing_cover_asset", "cover_asset_mismatch"}):
        return {
            "action": "sync_master_then_recheck",
            "command": (
                f"python -m bworkflow_sql sync {project_id} --step master && "
                f"python -m bworkflow_sql product-card-preflight {project_id} "
                f"--account {account} --product-card-template-id {template_id}"
            ),
        }
    return {
        "action": "regenerate_product_images_then_recheck",
        "command": (
            f"python -m bworkflow_sql product-images {project_id} "
            f"--account {account} --mode {regeneration_mode_for_issue_codes(codes)} "
            f"--product-card-template-id {template_id}"
        ),
    }
