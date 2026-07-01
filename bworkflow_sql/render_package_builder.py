from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .repositories import Repository
from .settings import INTERNAL_WORKSPACE_ROOT
from .subtitle_helpers import probe_media_duration_seconds
from .tts_helpers import DEFAULT_LOUDNORM_I, DEFAULT_LOUDNORM_LRA, DEFAULT_LOUDNORM_TP
from .utils import safe_text


SUPPORTED_OUTPUT_MODES = {"jianying_draft", "final_mp4"}
SUPPORTED_PRODUCT_MEDIA_MODES = {"cover_only", "video_preferred"}
DEFAULT_PRODUCT_MEDIA_MODE = "video_preferred"
PRODUCT_COVER_CACHE_ROOT = INTERNAL_WORKSPACE_ROOT / "product-covers"


@dataclass(frozen=True)
class ProductRenderPackageResult:
    package: dict[str, Any]
    missing: list[dict[str, Any]]


def build_product_recommendation_package(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    output_mode: str = "jianying_draft",
    product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
) -> ProductRenderPackageResult:
    if output_mode not in SUPPORTED_OUTPUT_MODES:
        raise ValueError(f"unsupported output_mode: {output_mode}")
    media_mode = safe_text(product_media_mode) or DEFAULT_PRODUCT_MEDIA_MODE
    if media_mode not in SUPPORTED_PRODUCT_MEDIA_MODES:
        raise ValueError(f"unsupported product_media_mode: {media_mode}")

    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    account = safe_text(account_label)
    products = repo.products(project_id, include_removed=False)
    blocks = repo.script_blocks(project_id)
    assets = repo.asset_bindings(project_id)
    product_blocks = {
        safe_text(block.get("owner_uid")): block
        for block in blocks
        if safe_text(block.get("script_type")) == "product"
    }
    price_blocks = [
        block
        for block in blocks
        if safe_text(block.get("script_type")) == "price_transition"
    ]

    missing: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []

    if not price_blocks:
        for label in _unique_price_labels(products):
            missing.append(
                {
                    "kind": "price_script",
                    "price_range_label": label,
                    "message": "missing price transition script",
                }
            )

    for block in price_blocks:
        voice = _ready_asset(
            assets,
            asset_type="voice",
            uid="PRICE_TRANSITION",
            account_label=account,
            script_block_id=int(block.get("id") or 0),
            text_hash=safe_text(block.get("text_hash")),
        )
        if not voice:
            missing.append(
                {
                    "kind": "price_voice",
                    "uid": "PRICE_TRANSITION",
                    "price_range_label": safe_text(block.get("price_range_label")),
                    "script_block_id": int(block.get("id") or 0),
                    "message": "missing ready voice for price transition script",
                }
            )
            continue
        voice_path = _absolute_file_path(voice.get("path"))
        segments.append(
            {
                "type": "price_transition",
                "id": f"price-{block.get('id')}",
                "priceRangeLabel": safe_text(block.get("price_range_label")),
                "transitionText": safe_text(block.get("body")),
                "voiceAsset": str(voice_path),
                "duration": get_audio_duration_seconds(voice_path),
                "sourceScriptBlockId": int(block.get("id") or 0),
            }
        )

    for product in products:
        uid = safe_text(product.get("uid"))
        title = safe_text(product.get("title"))
        block = product_blocks.get(uid)
        if not block:
            missing.append(
                {
                    "kind": "product_script",
                    "uid": uid,
                    "title": title,
                    "message": "missing product script",
                }
            )
            continue

        image = _ready_asset(
            assets,
            asset_type="image",
            uid=uid,
            account_label=account,
            allow_unscoped_account=True,
        )
        voice = _ready_asset(
            assets,
            asset_type="voice",
            uid=uid,
            account_label=account,
            script_block_id=int(block.get("id") or 0),
            text_hash=safe_text(block.get("text_hash")),
        )
        video = _ready_asset(assets, asset_type="video", uid=uid)
        product_missing = False
        if not image:
            product_missing = True
            missing.append(
                {
                    "kind": "product_image",
                    "uid": uid,
                    "title": title,
                    "message": "missing ready image for product",
                }
            )
        if not voice:
            product_missing = True
            missing.append(
                {
                    "kind": "product_voice",
                    "uid": uid,
                    "title": title,
                    "script_block_id": int(block.get("id") or 0),
                    "message": "missing ready voice for product script",
                }
            )
        if product_missing:
            continue

        voice_path = _absolute_file_path(voice.get("path"))
        image_path = _absolute_file_path(image.get("path"))
        video_path = (
            _absolute_file_path(video.get("path"))
            if media_mode == "video_preferred" and video
            else None
        )
        try:
            product_card = _product_card_payload(
                product,
                project=project,
                fallback_image_path=image_path,
            )
        except ValueError as exc:
            missing.append(
                {
                    "kind": "product_cover",
                    "uid": uid,
                    "title": title,
                    "message": str(exc),
                }
            )
            continue
        product_segment = {
            "type": "product_recommendation",
            "id": f"product-{uid}",
            "productUid": uid,
            "productTitle": title,
            "priceRangeLabel": safe_text(product.get("price_label")),
            "spokenText": safe_text(block.get("body")),
            "voiceAsset": str(voice_path),
            "imageCardAsset": str(image_path),
            "videoAsset": str(video_path) if video_path else None,
            "productMediaMode": media_mode,
            "duration": get_audio_duration_seconds(voice_path),
            "sourceScriptBlockId": int(block.get("id") or 0),
            "assetBindingIds": {
                "image": int(image.get("id") or 0),
                "voice": int(voice.get("id") or 0),
                "video": int(video.get("id") or 0) if video else None,
            },
            "subtitles": [],
        }
        if product_card:
            product_segment["productCard"] = product_card
        segments.append(product_segment)

    package = {
        "schemaVersion": "1.0.0",
        "packageType": "bilibili_video",
        "project": {
            "category": safe_text(project.get("category_name") or project.get("name")),
            "account": account,
            "bworkflowProjectId": int(project_id),
            "masterSchemeId": safe_text(project.get("scheme_id")),
        },
        "output": {
            "mode": output_mode,
            "productMediaMode": media_mode,
            "fps": 30,
            "width": 1920,
            "height": 1080,
        },
        "audio": {
            "loudnessTarget": {
                "integrated": DEFAULT_LOUDNORM_I,
                "truePeak": DEFAULT_LOUDNORM_TP,
                "lra": DEFAULT_LOUDNORM_LRA,
            }
        },
        "segments": segments,
        "assets": {},
        "approval": {
            "productRecommendationBatch": {
                "status": "pending",
                "reviewedAt": None,
            }
        },
    }
    return ProductRenderPackageResult(package=package, missing=missing)


def get_audio_duration_seconds(path: str | Path) -> float:
    return round(float(probe_media_duration_seconds(Path(path))), 3)


def _ready_asset(
    assets: list[dict[str, Any]],
    *,
    asset_type: str,
    uid: str,
    account_label: str = "",
    script_block_id: int | None = None,
    text_hash: str = "",
    allow_unscoped_account: bool = False,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if safe_text(asset.get("asset_type")) != asset_type:
            continue
        if safe_text(asset.get("status")) != "ready":
            continue
        if safe_text(asset.get("uid")) != uid:
            continue
        asset_account = safe_text(asset.get("account_label"))
        if account_label and asset_account != account_label:
            if not (allow_unscoped_account and not asset_account):
                continue
        if script_block_id is not None and int(asset.get("script_block_id") or 0) != script_block_id:
            continue
        if text_hash and safe_text(asset.get("text_hash")) != text_hash:
            continue
        path_text = safe_text(asset.get("path"))
        if not path_text or not Path(path_text).is_file():
            continue
        candidates.append(asset)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            safe_text(item.get("account_label")) != account_label,
            safe_text(item.get("path")),
        ),
    )[0]


def _absolute_file_path(value: Any) -> Path:
    path = Path(safe_text(value))
    return path if path.is_absolute() else path.resolve()


def _unique_price_labels(products: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for product in products:
        label = safe_text(product.get("price_label"))
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _product_card_payload(
    product: dict[str, Any],
    *,
    project: dict[str, Any],
    fallback_image_path: Path,
) -> dict[str, Any] | None:
    raw = safe_text(product.get("product_card_json"))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    data_map = payload.get("dataMap")
    slots = payload.get("slots")
    cover_asset = safe_text(payload.get("coverAsset"))
    if not cover_asset and isinstance(data_map, dict):
        cover_asset = safe_text(data_map.get("cover"))
    uid = safe_text(product.get("uid")) or "product"
    if _is_remote_url(cover_asset):
        cover_asset = str(
            _ensure_remote_cover_cached(
                cover_asset,
                category=safe_text(project.get("category_name") or project.get("name")),
                uid=uid,
            )
        )
    if not any([isinstance(data_map, dict), isinstance(slots, list), cover_asset]):
        return None

    normalized: dict[str, Any] = {
        "templateId": safe_text(payload.get("templateId")) or "xiaoran1",
        "dataMap": _string_map(data_map),
        "slots": _slot_list(slots),
        "fallbackImageAsset": str(fallback_image_path),
        "coverMediaSlot": {
            "x": 24,
            "y": 140,
            "width": 507,
            "height": 318,
            "sourceWidth": 970,
            "sourceHeight": 480,
        },
    }
    if cover_asset:
        normalized["coverAsset"] = cover_asset
        normalized["dataMap"]["cover"] = cover_asset
    return normalized


def _is_remote_url(value: str) -> bool:
    text = safe_text(value).lower()
    return text.startswith("http://") or text.startswith("https://")


def _ensure_remote_cover_cached(url: str, *, category: str, uid: str) -> Path:
    target = _cover_cache_path(category=category, uid=uid, url=url)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_bytes(_download_url_bytes(url))
    except Exception as exc:  # pragma: no cover - exercised through caller behavior.
        raise ValueError(f"failed to download product cover for {uid}: {url}: {exc}") from exc
    return target


def _cover_cache_path(*, category: str, uid: str, url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    return (
        PRODUCT_COVER_CACHE_ROOT
        / _safe_path_component(category or "uncategorized")
        / f"{_safe_path_component(uid or 'product')}{suffix}"
    )


def _download_url_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def _safe_path_component(value: str) -> str:
    text = safe_text(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "uncategorized"


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        safe_text(key): safe_text(item)
        for key, item in value.items()
        if safe_text(key) and safe_text(item)
    }


def _slot_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    slots: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = safe_text(item.get("label"))
        slot_value = safe_text(item.get("value"))
        if label and slot_value:
            slots.append({"label": label, "value": slot_value})
    return slots
