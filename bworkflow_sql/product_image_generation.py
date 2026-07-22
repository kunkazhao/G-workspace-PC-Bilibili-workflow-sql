from __future__ import annotations

import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from .asset_paths import project_category_folder
from .cutme_adapter import CutMeAdapter
from .db import Database
from .render_package_builder import (
    product_card_content_fingerprint,
    product_card_payload_for_product,
)
from .resource_lifecycle import (
    DERIVED_ASSET_RETENTION_DAYS,
    register_cleanup_candidate_in_connection,
    register_completed_product_image_job,
)
from .repositories import Repository
from .settings import DEFAULT_IMAGE_ROOT, INTERNAL_WORKSPACE_ROOT
from .template_config import available_templates, image_set_for_template, resolve_product_card_template
from .utils import file_metadata, now_iso, safe_text

PRODUCT_IMAGE_RENDER_JOB_ROOT = INTERNAL_WORKSPACE_ROOT / "product-image-jobs"

ProductCardStillRenderer = Callable[[Path, str, Path], Path]


def regenerate_product_card_images(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    mode: str = "stale",
    product_uid: str = "",
    product_card_template_id: str = "",
    render_product_card_still: ProductCardStillRenderer | None = None,
    max_workers: int = 1,
) -> dict[str, Any]:
    total_started = perf_counter()
    mode_value = safe_text(mode) or "stale"
    if mode_value not in {"stale", "missing", "all"}:
        raise ValueError(f"unsupported product image regenerate mode: {mode_value}")
    target_uid = safe_text(product_uid)

    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    renderer = render_product_card_still
    selected_template = resolve_product_card_template(
        account_label,
        product_card_template_id,
        require_explicit=True,
    )
    selected_template_id = safe_text(selected_template.get("templateId")) or safe_text(product_card_template_id)
    default_image_set = _default_image_set_for_account(
        account_label,
        product_card_template_id=selected_template_id,
    )
    assets = repo.asset_bindings(project_id)
    accounts = {safe_text(item.get("label")): item for item in repo.accounts()}
    regenerated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    products = repo.products(project_id, include_removed=False)
    if target_uid:
        products = [product for product in products if safe_text(product.get("uid")) == target_uid]
        if not products:
            raise ValueError(f"product uid does not exist in project {project_id}: {target_uid}")

    for product in products:
        uid = safe_text(product.get("uid"))
        default_image_path = _default_image_output_path(
            project=project,
            product=product,
            account_label=account_label,
            image_set=default_image_set,
        )
        image = _ready_image_asset(
            assets,
            uid=uid,
            account_label=account_label,
            preferred_image_set=default_image_set,
        )
        image_matches_default_template = bool(
            image
            and _image_path_uses_template(
                Path(safe_text(image.get("path"))),
                account_label=account_label,
                image_set=default_image_set,
            )
        )
        if not image:
            if mode_value not in {"missing", "all"}:
                skipped.append({"uid": uid, "reason": "missing_ready_image_binding"})
                continue
            image_path = default_image_path
            regenerate_reason = "missing_ready_image_binding"
            binding_id = None
        elif not image_matches_default_template:
            image_path = default_image_path
            regenerate_reason = "wrong_template_binding"
            binding_id = int(image.get("id") or 0)
        else:
            image_path = Path(safe_text(image.get("path")))
            regenerate_reason = "stale_or_forced"
            binding_id = int(image.get("id") or 0)
        product_card = product_card_payload_for_product(
            product,
            project=project,
            fallback_image_path=image_path,
            account_label=account_label,
            product_card_template_id=selected_template_id,
        )
        if not product_card:
            skipped.append({"uid": uid, "reason": "missing_product_card"})
            continue
        fingerprint = product_card_content_fingerprint(product, product_card)
        stored_fingerprint = safe_text(image.get("text_hash")) if image else ""
        is_unknown_legacy_hash = bool(image and image_matches_default_template and not stored_fingerprint)
        is_stale = bool(
            (stored_fingerprint and stored_fingerprint != fingerprint)
            or is_unknown_legacy_hash
            or (image and not image_matches_default_template)
        )
        if mode_value == "stale" and not is_stale:
            skipped.append({"uid": uid, "reason": "not_stale"})
            continue
        if mode_value == "missing" and image and image_matches_default_template:
            skipped.append({"uid": uid, "reason": "already_has_ready_image_binding"})
            continue

        if is_unknown_legacy_hash:
            regenerate_reason = "unknown_legacy_image_hash"
        pending.append(
            {
                "uid": uid,
                "image_path": image_path,
                "binding_id": binding_id,
                "product": product,
                "product_card": product_card,
                "fingerprint": fingerprint,
                "reason": regenerate_reason,
            }
        )

    package_path = _write_product_card_batch_job_package(project=project, items=pending) if pending else None
    for item in pending:
        item["package_path"] = package_path

    prepare_seconds = round(perf_counter() - total_started, 3)
    requested_workers = max(1, min(int(max_workers), 4))
    used_workers = min(requested_workers, len(pending)) if pending else 0

    def render_pending(item: dict[str, Any]) -> None:
        if renderer is None:
            raise RuntimeError("individual renderer is not configured")
        renderer(item["package_path"], item["uid"], item["image_path"])

    render_started = perf_counter()
    if used_workers and renderer is None:
        CutMeAdapter().render_product_cards(
            package_path,
            [(item["uid"], item["image_path"]) for item in pending],
            max_workers=used_workers,
        )
    elif used_workers == 1:
        for item in pending:
            render_pending(item)
    elif used_workers > 1:
        with ThreadPoolExecutor(max_workers=used_workers, thread_name_prefix="product-card") as executor:
            list(executor.map(render_pending, pending))
    render_seconds = round(perf_counter() - render_started, 3)

    for item in pending:
        image_path = item["image_path"]
        if not image_path.is_file():
            raise RuntimeError(f"product card renderer did not create image: {image_path}")
        _upsert_image_binding_ready(
            db,
            binding_id=item["binding_id"],
            project_id=project_id,
            product=item["product"],
            account_label=account_label,
            account=accounts.get(safe_text(account_label), {}),
            image_path=image_path,
            fingerprint=item["fingerprint"],
        )
        regenerated.append(
            {
                "uid": item["uid"],
                "title": safe_text(item["product"].get("title")),
                "path": str(image_path),
                "fingerprint": item["fingerprint"],
                "package_path": str(item["package_path"]),
                "reason": item["reason"],
            }
        )

    lifecycle = {"status": "not_applicable", "candidate_registered": False}
    if package_path is not None:
        try:
            register_completed_product_image_job(
                db,
                project_id=project_id,
                package_path=package_path,
            )
            lifecycle = {
                "status": "registered",
                "candidate_registered": True,
                "resource_kind": "product_image_job",
                "path": str(package_path.parent),
            }
        except Exception as exc:
            lifecycle = {
                "status": "warning",
                "candidate_registered": False,
                "message": f"resource lifecycle registration failed: {exc}",
            }

    return {
        "ok": True,
        "project_id": project_id,
        "account": account_label,
        "mode": mode_value,
        "product_uid": target_uid or None,
        "product_card_template_id": selected_template_id or None,
        "workers": {"requested": requested_workers, "used": used_workers},
        "timings": {
            "prepare_seconds": prepare_seconds,
            "render_seconds": render_seconds,
            "total_seconds": round(perf_counter() - total_started, 3),
        },
        "regenerated": regenerated,
        "skipped": skipped,
        "resource_lifecycle": lifecycle,
    }


def _ready_image_asset(
    assets: list[dict[str, Any]],
    *,
    uid: str,
    account_label: str,
    preferred_image_set: str = "",
) -> dict[str, Any] | None:
    account = safe_text(account_label)
    image_set = safe_text(preferred_image_set)
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if safe_text(asset.get("asset_type")) != "image":
            continue
        if safe_text(asset.get("status")) != "ready":
            continue
        if safe_text(asset.get("uid")) != uid:
            continue
        asset_account = safe_text(asset.get("account_label"))
        if account and asset_account not in {account, ""}:
            continue
        path = Path(safe_text(asset.get("path")))
        if not path.is_file():
            continue
        candidates.append(asset)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            (
                not _image_path_uses_template(
                    Path(safe_text(item.get("path"))),
                    account_label=account,
                    image_set=image_set,
                )
                if image_set
                else False
            ),
            safe_text(item.get("account_label")) != account,
            safe_text(item.get("path")),
        ),
    )[0]


def _write_product_card_batch_job_package(
    *,
    project: dict[str, Any],
    items: list[dict[str, Any]],
) -> Path:
    job_root = (
        PRODUCT_IMAGE_RENDER_JOB_ROOT
        / f"project-{int(project.get('id') or 0)}"
        / f"batch-{time.time_ns()}"
    )
    job_root.mkdir(parents=True, exist_ok=True)
    segments: list[dict[str, Any]] = []
    for item in items:
        product = item["product"]
        uid = safe_text(product.get("uid")) or "product"
        local_card = _localize_product_card_assets(
            item["product_card"],
            job_root=job_root,
            product=product,
            project=project,
        )
        segments.append(
            {
                "type": "product_recommendation",
                "id": f"product-{uid}",
                "productUid": uid,
                "productTitle": safe_text(product.get("title")),
                "priceRangeLabel": safe_text(product.get("price_label")),
                "spokenText": "",
                "voiceAsset": None,
                "imageCardAsset": None,
                "videoAsset": None,
                "productMediaMode": "cover_only",
                "duration": 1,
                "productCard": local_card,
                "productCardFingerprint": item["fingerprint"],
                "subtitles": [],
            }
        )
    package = {
        "schemaVersion": "1.0.0",
        "packageType": "bilibili_video",
        "project": {
            "category": project_category_folder(project),
            "account": "",
            "bworkflowProjectId": int(project.get("id") or 0),
            "masterSchemeId": safe_text(project.get("scheme_id")),
        },
        "output": {
            "mode": "product_card_still",
            "productMediaMode": "cover_only",
            "fps": 30,
            "width": 1920,
            "height": 1080,
        },
        "audio": {"loudnessTarget": {"integrated": -11, "truePeak": -1.0, "lra": 11}},
        "segments": segments,
        "assets": {},
        "approval": {},
    }
    package_path = job_root / "render-package.json"
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return package_path


def _default_image_output_path(
    *,
    project: dict[str, Any],
    product: dict[str, Any],
    account_label: str,
    image_set: str = "",
) -> Path:
    root = Path(safe_text(project.get("image_root")) or DEFAULT_IMAGE_ROOT)
    category = project_category_folder(project)
    template = safe_text(image_set) or _default_image_set_for_account(account_label)
    uid = safe_text(product.get("uid")) or "product"
    title = safe_text(product.get("title")) or uid
    price = _filename_price_label(safe_text(product.get("price_label")))
    filename = _safe_path_component("-".join(part for part in [price, uid, title] if part)) + ".png"
    return root / _safe_path_component(category) / _safe_path_component(account_label) / template / filename


def _default_image_set_for_account(account_label: str, *, product_card_template_id: str = "") -> str:
    selected_template = resolve_product_card_template(account_label, product_card_template_id)
    if selected_template:
        display_name = safe_text(selected_template.get("displayName"))
        if display_name:
            return _safe_path_component(image_set_for_template(display_name))
    templates = available_templates(safe_text(account_label))
    if templates:
        return _safe_path_component(image_set_for_template(templates[0]))
    return "模板1"


def _image_path_uses_template(path: Path, *, account_label: str, image_set: str) -> bool:
    account = _safe_path_component(account_label)
    template = _safe_path_component(image_set)
    parts = [safe_text(part) for part in path.parts]
    for index, part in enumerate(parts[:-1]):
        if part == account and parts[index + 1] == template:
            return True
    return False


def _filename_price_label(value: str) -> str:
    text = safe_text(value)
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _localize_product_card_assets(
    product_card: dict[str, Any],
    *,
    job_root: Path,
    product: dict[str, Any],
    project: dict[str, Any],
) -> dict[str, Any]:
    local_card = json.loads(json.dumps(product_card, ensure_ascii=False))
    cover = safe_text(local_card.get("coverAsset"))
    if not cover:
        data_map = (
            local_card.get("dataMap")
            if isinstance(local_card.get("dataMap"), dict)
            else {}
        )
        cover = safe_text(data_map.get("cover"))
    cover_path = Path(cover)
    if cover and cover_path.is_file():
        relative = _copy_job_asset(
            cover_path,
            job_root=job_root,
            target_dir=(
                job_root
                / "assets"
                / "product-covers"
                / _safe_path_component(project_category_folder(project))
                / _safe_path_component(safe_text(product.get("uid")) or "product")
            ),
            fallback_name=(
                _safe_path_component(safe_text(product.get("uid")) or "product")
                + cover_path.suffix
            ),
        )
        local_card["coverAsset"] = relative
        data_map = local_card.setdefault("dataMap", {})
        if isinstance(data_map, dict):
            data_map["cover"] = relative
    return local_card


def _copy_job_asset(
    source: Path,
    *,
    job_root: Path,
    target_dir: Path,
    fallback_name: str,
) -> str:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / (source.name if source.name else fallback_name)
    if target.name in {"", ".", ".."}:
        target = target_dir / fallback_name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target.relative_to(job_root).as_posix()


def _upsert_image_binding_ready(
    db: Database,
    *,
    binding_id: int | None,
    project_id: int,
    product: dict[str, Any],
    account_label: str,
    account: dict[str, Any],
    image_path: Path,
    fingerprint: str,
) -> None:
    meta = file_metadata(image_path)
    ts = now_iso()
    with db.connect() as conn:
        if binding_id:
            existing = conn.execute(
                "SELECT path, source_kind FROM asset_bindings WHERE id=?",
                (binding_id,),
            ).fetchone()
            previous_path = Path(safe_text(existing["path"])) if existing else None
            previous_source = safe_text(existing["source_kind"]) if existing else ""
            conn.execute(
                """
                UPDATE asset_bindings
                SET text_hash=?,
                    path=?,
                    status='ready',
                    source_kind='remotion',
                    file_size=?,
                    file_mtime=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    fingerprint,
                    str(image_path),
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    binding_id,
                ),
            )
            if (
                previous_path
                and previous_source != "manual"
                and previous_path != image_path
                and previous_path.is_file()
            ):
                register_cleanup_candidate_in_connection(
                    conn,
                    project_id=project_id,
                    resource_kind="replaced_product_image",
                    path=previous_path,
                    reason="image_binding_moved_to_new_template_or_output_path",
                    eligible_at=datetime.now(timezone.utc)
                    + timedelta(days=DERIVED_ASSET_RETENTION_DAYS),
                    details={"replacement_path": str(image_path), "binding_id": binding_id},
                )
            return
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, text_hash, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
            VALUES (?, ?, 'image', ?, ?, ?, ?, 'ready', 'remotion', ?, ?, 1, ?, ?)
            ON CONFLICT(project_id, uid, script_block_id, asset_type, account_label, block_label, path)
            DO UPDATE SET
                account_id=excluded.account_id,
                text_hash=excluded.text_hash,
                status='ready',
                source_kind='remotion',
                file_size=excluded.file_size,
                file_mtime=excluded.file_mtime,
                confirmed=1,
                updated_at=excluded.updated_at
            """,
            (
                project_id,
                safe_text(product.get("uid")),
                safe_text(account.get("label")) or safe_text(account_label),
                safe_text(account.get("account_id")),
                fingerprint,
                str(image_path),
                meta["file_size"],
                meta["file_mtime"],
                ts,
                ts,
            ),
        )


def _safe_path_component(value: str) -> str:
    text = safe_text(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "item"
