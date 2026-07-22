from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .db import Database
from .dynamic_product_card import (
    DynamicPreflightIssue,
    build_dynamic_product_context,
    category_leaf_name,
    validate_price_ranges,
)
from .master_contracts import MasterContractAdapter, MasterContractError
from .repositories import Repository
from .template_config import get_remotion_template_metadata, product_card_slot_issues
from .utils import safe_text


def dynamic_product_card_preflight(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    product_card_template_id: str,
    master_contracts: MasterContractAdapter | None = None,
    product_uid: str = "",
) -> dict[str, Any]:
    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        return _failure(
            project_id=project_id,
            account_label=account_label,
            template_id=product_card_template_id,
            error_code="project_not_found",
            issues=(
                DynamicPreflightIssue(
                    code="project_not_found",
                    product_uid="",
                    field="project_id",
                    message=f"Project does not exist: {project_id}",
                ),
            ),
        )

    workspace_id = safe_text(project.get("workspace_id"))
    scheme_id = safe_text(project.get("scheme_id"))
    if not workspace_id or not scheme_id:
        return _failure(
            project_id=project_id,
            account_label=account_label,
            template_id=product_card_template_id,
            error_code="master_identity_missing",
            issues=(
                DynamicPreflightIssue(
                    code="master_identity_missing",
                    product_uid="",
                    field="workspace_id" if not workspace_id else "scheme_id",
                    message="Project must declare both Master workspace_id and scheme_id.",
                ),
            ),
        )

    template_id = safe_text(product_card_template_id)
    try:
        get_remotion_template_metadata(template_id)
    except (OSError, ValueError) as exc:
        return _failure(
            project_id=project_id,
            account_label=account_label,
            template_id=template_id,
            error_code="invalid_product_card_template",
            issues=(
                DynamicPreflightIssue(
                    code="invalid_product_card_template",
                    product_uid="",
                    field="product_card_template_id",
                    message=str(exc),
                ),
            ),
        )

    adapter = master_contracts or MasterContractAdapter()
    try:
        snapshot = adapter.fetch_scheme_snapshot(
            workspace_id,
            scheme_id,
            force_refresh=True,
        )
    except MasterContractError as exc:
        return _failure(
            project_id=project_id,
            account_label=account_label,
            template_id=template_id,
            error_code=exc.code,
            issues=(
                DynamicPreflightIssue(
                    code=exc.code,
                    product_uid="",
                    field="master_snapshot",
                    message=str(exc),
                ),
            ),
        )
    except Exception as exc:
        return _failure(
            project_id=project_id,
            account_label=account_label,
            template_id=template_id,
            error_code="master_snapshot_failed",
            issues=(
                DynamicPreflightIssue(
                    code="master_snapshot_failed",
                    product_uid="",
                    field="master_snapshot",
                    message=str(exc),
                ),
            ),
        )

    identity_issues: list[DynamicPreflightIssue] = []
    if safe_text(snapshot.workspace.id) != workspace_id:
        identity_issues.append(
            DynamicPreflightIssue(
                code="snapshot_identity_mismatch",
                product_uid="",
                field="snapshot.workspace.id",
                message="Master snapshot workspace does not match the current project.",
            )
        )
    if safe_text(snapshot.scheme.id) != scheme_id:
        identity_issues.append(
            DynamicPreflightIssue(
                code="snapshot_identity_mismatch",
                product_uid="",
                field="snapshot.scheme.id",
                message="Master snapshot scheme does not match the current project.",
            )
        )

    selected_uid = safe_text(product_uid)
    products = [
        product
        for product in repo.products(project_id, include_removed=False)
        if not selected_uid or safe_text(product.get("uid")) == selected_uid
    ]
    issues: list[DynamicPreflightIssue] = list(identity_issues)
    if not products:
        issues.append(
            DynamicPreflightIssue(
                code="product_uid_not_found" if selected_uid else "no_active_products",
                product_uid=selected_uid,
                field="products",
                message=(
                    "Selected product UID is not active in this project."
                    if selected_uid
                    else "Project has no active products to render."
                ),
            )
        )

    parsed_ranges, range_issues = validate_price_ranges(snapshot.price_ranges)
    issues.extend(range_issues)
    ranges_valid = not range_issues

    snapshot_by_uid: dict[str, Any] = {}
    for snapshot_product in snapshot.products:
        uid = safe_text(snapshot_product.uid)
        if uid in snapshot_by_uid:
            issues.append(
                DynamicPreflightIssue(
                    code="duplicate_snapshot_product_uid",
                    product_uid=uid,
                    field="snapshot.products",
                    message="Master snapshot contains the same UID more than once.",
                )
            )
            continue
        snapshot_by_uid[uid] = snapshot_product

    assets = repo.asset_bindings(project_id)
    blocks = repo.script_blocks(project_id)
    blocks_by_uid: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        if safe_text(block.get("script_type")) == "product":
            blocks_by_uid.setdefault(safe_text(block.get("owner_uid")), []).append(block)

    category_source = safe_text(project.get("category_name")) or safe_text(
        snapshot.scheme.category.name
    )
    contexts: list[dict[str, Any]] = []
    for local_product in products:
        uid = safe_text(local_product.get("uid"))
        snapshot_product = snapshot_by_uid.get(uid)
        if snapshot_product is None:
            issues.append(
                DynamicPreflightIssue(
                    code="snapshot_product_missing",
                    product_uid=uid,
                    field="snapshot.products",
                    message="Active local product UID is missing from the current Master snapshot.",
                )
            )
            continue

        block, voice_asset = _select_script_and_voice(
            blocks_by_uid.get(uid, []),
            assets,
            uid=uid,
            account_label=account_label,
        )
        if block is None:
            issues.append(
                DynamicPreflightIssue(
                    code="missing_product_script",
                    product_uid=uid,
                    field="script",
                    message="Active product has no current product script block.",
                )
            )
        media_kind, media_asset = _select_product_media(snapshot_product, assets)
        context, product_issues = build_dynamic_product_context(
            snapshot_product,
            parsed_price_ranges=parsed_ranges,
            category_label=category_leaf_name(category_source),
            media_kind=media_kind,
            media_asset=media_asset,
            voice_asset=voice_asset,
            spoken_text=safe_text(block.get("body")) if block else "",
            source_script_block_id=int(block.get("id") or 0) if block else 0,
        )
        issues.extend(product_issues)
        if context is None or not ranges_valid:
            continue

        required_issues = product_card_slot_issues(
            template_id,
            context.template_validation_card(),
        )
        if required_issues:
            issues.extend(
                DynamicPreflightIssue(
                    code=safe_text(item.get("code")) or "missing_required_product_card_slot",
                    product_uid=uid,
                    field=safe_text(item.get("slot_key")) or "slot",
                    message=safe_text(item.get("message")),
                )
                for item in required_issues
            )
            continue
        contexts.append(context.as_dict())

    issue_dicts = [item.as_dict() for item in issues]
    ok = not issues
    return {
        "ok": ok,
        "status": "ready" if ok else "blocked",
        "error_code": None if ok else "dynamic_product_preflight_failed",
        "project_id": project_id,
        "account": safe_text(account_label),
        "product_card_template_id": template_id,
        "snapshot_id": safe_text(snapshot.snapshot_id),
        "summary": {
            "errors": len(issue_dicts),
            "products_checked": len(products),
            "contexts_ready": len(contexts),
        },
        "issues": issue_dicts,
        "contexts": contexts,
        "next": None,
    }


def product_card_preflight(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    product_card_template_id: str,
    product_uid: str = "",
    expect_cover: str = "",
    master_contracts: MasterContractAdapter | None = None,
) -> dict[str, Any]:
    # `expect_cover` is retained only as a CLI/API compatibility argument. The
    # current Master snapshot is authoritative for dynamic rendering.
    _ = expect_cover
    return dynamic_product_card_preflight(
        db,
        project_id=project_id,
        account_label=account_label,
        product_card_template_id=product_card_template_id,
        master_contracts=master_contracts,
        product_uid=product_uid,
    )


def _failure(
    *,
    project_id: int,
    account_label: str,
    template_id: str,
    error_code: str,
    issues: tuple[DynamicPreflightIssue, ...],
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "blocked",
        "error_code": error_code,
        "project_id": project_id,
        "account": safe_text(account_label),
        "product_card_template_id": safe_text(template_id),
        "snapshot_id": None,
        "summary": {
            "errors": len(issues),
            "products_checked": 0,
            "contexts_ready": 0,
        },
        "issues": [item.as_dict() for item in issues],
        "contexts": [],
        "next": None,
    }


def _select_script_and_voice(
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    *,
    uid: str,
    account_label: str,
) -> tuple[dict[str, Any] | None, str]:
    ordered = sorted(blocks, key=lambda item: int(item.get("id") or 0), reverse=True)
    for block in ordered:
        block_id = int(block.get("id") or 0)
        block_hash = safe_text(block.get("text_hash"))
        for asset in assets:
            if safe_text(asset.get("asset_type")) != "voice":
                continue
            if safe_text(asset.get("uid")) != uid:
                continue
            if safe_text(asset.get("account_label")) != safe_text(account_label):
                continue
            if safe_text(asset.get("status")) != "ready":
                continue
            if int(asset.get("script_block_id") or 0) != block_id:
                continue
            if block_hash and safe_text(asset.get("text_hash")) != block_hash:
                continue
            path = Path(safe_text(asset.get("path")))
            if path.is_file():
                return block, str(path.resolve())
    return (ordered[0] if ordered else None), ""


def _select_product_media(
    snapshot_product: Any,
    assets: list[dict[str, Any]],
) -> tuple[str, str]:
    uid = safe_text(snapshot_product.uid)
    for asset in assets:
        if safe_text(asset.get("asset_type")) != "video":
            continue
        if safe_text(asset.get("uid")) != uid:
            continue
        if safe_text(asset.get("status")) != "ready":
            continue
        path = Path(safe_text(asset.get("path")))
        if path.is_file():
            return "video", str(path.resolve())

    cover = safe_text(snapshot_product.card.cover_url)
    if _valid_cover(cover):
        if _is_http_url(cover):
            return "cover", cover
        return "cover", str(Path(cover).resolve())
    return "", ""


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _valid_cover(value: str) -> bool:
    return bool(value) and (_is_http_url(value) or Path(value).is_file())
