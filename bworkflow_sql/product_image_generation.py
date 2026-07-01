from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .asset_paths import project_category_folder
from .db import Database
from .render_package_builder import (
    product_card_content_fingerprint,
    product_card_payload_for_product,
)
from .repositories import Repository
from .settings import CUTME_ROOT, INTERNAL_WORKSPACE_ROOT
from .utils import file_metadata, now_iso, safe_text

PRODUCT_IMAGE_RENDER_JOB_ROOT = INTERNAL_WORKSPACE_ROOT / "product-image-jobs"
PRODUCT_IMAGE_RENDER_TIMEOUT = 180
_NPM = "npm.cmd" if platform.system() == "Windows" else "npm"

ProductCardStillRenderer = Callable[[Path, str, Path], Path]


def regenerate_product_card_images(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    mode: str = "stale",
    render_product_card_still: ProductCardStillRenderer | None = None,
) -> dict[str, Any]:
    mode_value = safe_text(mode) or "stale"
    if mode_value not in {"stale", "all"}:
        raise ValueError(f"unsupported product image regenerate mode: {mode_value}")

    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    renderer = render_product_card_still or render_product_card_still_via_cutme
    assets = repo.asset_bindings(project_id)
    regenerated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for product in repo.products(project_id, include_removed=False):
        uid = safe_text(product.get("uid"))
        image = _ready_image_asset(assets, uid=uid, account_label=account_label)
        if not image:
            skipped.append({"uid": uid, "reason": "missing_ready_image_binding"})
            continue
        image_path = Path(safe_text(image.get("path")))
        product_card = product_card_payload_for_product(
            product,
            project=project,
            fallback_image_path=image_path,
        )
        if not product_card:
            skipped.append({"uid": uid, "reason": "missing_product_card"})
            continue
        fingerprint = product_card_content_fingerprint(product, product_card)
        stored_fingerprint = safe_text(image.get("text_hash"))
        is_stale = bool(stored_fingerprint and stored_fingerprint != fingerprint)
        if mode_value == "stale" and not is_stale:
            skipped.append({"uid": uid, "reason": "not_stale"})
            continue

        package_path = _write_product_card_job_package(
            project=project,
            product=product,
            product_card=product_card,
            fingerprint=fingerprint,
        )
        renderer(package_path, uid, image_path)
        if not image_path.is_file():
            raise RuntimeError(f"product card renderer did not create image: {image_path}")
        _mark_image_binding_ready(
            db,
            binding_id=int(image.get("id") or 0),
            image_path=image_path,
            fingerprint=fingerprint,
        )
        regenerated.append(
            {
                "uid": uid,
                "title": safe_text(product.get("title")),
                "path": str(image_path),
                "fingerprint": fingerprint,
                "package_path": str(package_path),
            }
        )

    return {
        "ok": True,
        "project_id": project_id,
        "account": account_label,
        "mode": mode_value,
        "regenerated": regenerated,
        "skipped": skipped,
    }


def render_product_card_still_via_cutme(
    package_path: Path,
    product_uid: str,
    output_path: Path,
) -> Path:
    renderer_root = CUTME_ROOT / "remotion-renderer"
    command = [
        _NPM,
        "run",
        "product-card:job",
        "--",
        "--package-path",
        str(package_path),
        "--product-uid",
        product_uid,
        "--out",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=str(renderer_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PRODUCT_IMAGE_RENDER_TIMEOUT,
        )
    except FileNotFoundError:
        raise FileNotFoundError("npm is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"product card still render timed out after {PRODUCT_IMAGE_RENDER_TIMEOUT}s") from None
    if result.returncode != 0:
        details = "\n".join(
            item for item in [result.stdout.strip(), result.stderr.strip()] if item
        )
        raise RuntimeError(details or f"product card still render failed: {result.returncode}")
    if not output_path.is_file():
        raise RuntimeError(f"product card still render did not create output: {output_path}")
    return output_path


def _ready_image_asset(
    assets: list[dict[str, Any]],
    *,
    uid: str,
    account_label: str,
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
            safe_text(item.get("account_label")) != account,
            safe_text(item.get("path")),
        ),
    )[0]


def _write_product_card_job_package(
    *,
    project: dict[str, Any],
    product: dict[str, Any],
    product_card: dict[str, Any],
    fingerprint: str,
) -> Path:
    uid = safe_text(product.get("uid")) or "product"
    job_root = (
        PRODUCT_IMAGE_RENDER_JOB_ROOT
        / f"project-{int(project.get('id') or 0)}"
        / f"{_safe_path_component(uid)}-{int(time.time() * 1000)}"
    )
    job_root.mkdir(parents=True, exist_ok=True)
    local_card = _localize_product_card_assets(
        product_card,
        job_root=job_root,
        product=product,
        project=project,
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
        "segments": [
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
                "productCardFingerprint": fingerprint,
                "subtitles": [],
            }
        ],
        "assets": {},
        "approval": {},
    }
    package_path = job_root / "render-package.json"
    package_path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return package_path


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


def _mark_image_binding_ready(
    db: Database,
    *,
    binding_id: int,
    image_path: Path,
    fingerprint: str,
) -> None:
    meta = file_metadata(image_path)
    ts = now_iso()
    with db.connect() as conn:
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


def _safe_path_component(value: str) -> str:
    text = safe_text(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "item"
