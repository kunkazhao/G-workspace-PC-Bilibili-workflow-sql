from __future__ import annotations

import json
import random
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .repositories import Repository
from .settings import INTERNAL_WORKSPACE_ROOT
from .subtitle_helpers import distribute_subtitle_text, probe_media_duration_seconds
from .template_config import display_template_from_image_path, get_template_slot
from .tts_helpers import DEFAULT_LOUDNORM_I, DEFAULT_LOUDNORM_LRA, DEFAULT_LOUDNORM_TP
from .utils import safe_text, text_hash


SUPPORTED_OUTPUT_MODES = {"jianying_draft", "final_mp4"}
SUPPORTED_PRODUCT_MEDIA_MODES = {"cover_only", "video_preferred"}
DEFAULT_PRODUCT_MEDIA_MODE = "video_preferred"
GLOBAL_SUBTITLE_STYLE_IDS = ("clean_white", "bold_yellow", "cyan_focus")
PRODUCT_COVER_CACHE_ROOT = INTERNAL_WORKSPACE_ROOT / "product-covers"
PRICE_TRANSITION_KEYWORDS = [
    "品牌完成度",
    "音质细节",
    "通话",
    "连接",
    "漏音控制",
    "续航",
    "调音",
    "通话降噪",
    "佩戴",
    "音质",
    "省心",
    "基础功能",
    "基础体验",
    "长期用",
    "尝鲜",
    "少花钱",
]
PRICE_TRANSITION_PARAMETER_GROUPS = [
    ("品牌完成度", ("品牌完成度",)),
    ("音质细节", ("音质细节",)),
    ("通话 / 连接 / 漏音控制", ("通话", "连接", "漏音控制")),
    ("音质解析编码", ("音质解析编码", "解析编码")),
    ("外观做工质感", ("外观", "做工质感")),
    ("性价比", ("性价比",)),
    ("音质表现", ("音质",)),
    ("降噪", ("降噪",)),
    ("高端型号", ("高端型号", "高端")),
    ("睡眠场景", ("睡眠",)),
    ("玩法", ("玩法",)),
    ("预算充足", ("预算充足",)),
    ("续航", ("续航",)),
    ("调音", ("调音",)),
    ("通话降噪", ("通话降噪",)),
    ("佩戴体验", ("佩戴", "佩戴体验")),
    ("基础功能", ("基础功能",)),
    ("基础体验", ("基础体验",)),
    ("少花钱试戴法", ("花最少的钱", "少花钱")),
]


@dataclass(frozen=True)
class ProductRenderPackageResult:
    package: dict[str, Any]
    missing: list[dict[str, Any]]
    stale_product_images: list[dict[str, Any]]


def _trim_transition_text(text: str) -> str:
    return safe_text(text).strip(" ，。；、,.!?:：")


def _split_transition_text(text: str) -> list[str]:
    parts: list[str] = []
    for sentence in re.split(r"[。；;！!？?]", safe_text(text)):
        parts.extend(re.split(r"[，,]", sentence))
    return [_trim_transition_text(part) for part in parts if _trim_transition_text(part)]


def _compact_transition_point(text: str) -> str:
    value = _trim_transition_text(text)
    value = re.sub(r"^重点看", "", value)
    value = re.sub(r"^核心就一件事", "核心", value)
    value = re.sub(r"^(这个价位|这个区间)", "", value)
    return value[:80] if re.search(r"[A-Za-z]", value) else value[:12]


def _price_transition_headline(label: str, body: str) -> str:
    chunks = _split_transition_text(body)
    preferred = next(
        (
            chunk
            for chunk in chunks
            if re.search(r"性价比|重点|核心|明显提升|旗舰|高端|够用|稳|focuses|maturity", chunk, re.I)
        ),
        chunks[1] if len(chunks) > 1 else (chunks[0] if chunks else ""),
    )
    headline = _trim_transition_text(preferred.replace(safe_text(label), ""))
    headline = re.sub(r"^下面(先)?(看|是)", "", headline)
    headline = re.sub(r"^(这个价位|这个区间)", "", headline)
    return _trim_transition_text(headline) or "先看这个价位的核心取舍"


def _price_transition_key_points(body: str) -> list[str]:
    text = safe_text(body)
    found = [keyword for keyword in PRICE_TRANSITION_KEYWORDS if keyword in text]
    chunks = [_compact_transition_point(chunk) for chunk in _split_transition_text(text)]
    result: list[str] = []
    for item in [*found, *chunks]:
        if item and item not in result:
            result.append(item)
        if len(result) >= 3:
            break
    return result or ["核心取舍"]


def _price_transition_audience(body: str) -> str:
    match = re.search(r"适合([^。；;，,]+?)(?:。|；|;|，|,|$)", safe_text(body))
    if match:
        return f"适合{_trim_transition_text(match.group(1))}"
    return "按预算和使用频率来选"


def _parameter_match_index(text: str, triggers: tuple[str, ...]) -> tuple[int, str] | None:
    matches = [(text.find(trigger), trigger) for trigger in triggers if trigger and trigger in text]
    if not matches:
        return None
    return min(matches, key=lambda item: item[0])


def _price_transition_parameter_items(body: str, duration: float) -> list[dict[str, Any]]:
    text = safe_text(body)
    text_length = max(len(text), 1)
    detected: list[tuple[int, str, str]] = []
    matched_labels: set[str] = set()
    for label, triggers in PRICE_TRANSITION_PARAMETER_GROUPS:
        match = _parameter_match_index(text, triggers)
        if match:
            if label == "音质表现" and matched_labels.intersection({"音质细节", "音质解析编码"}):
                continue
            if label == "降噪" and matched_labels.intersection({"通话 / 连接 / 漏音控制", "通话降噪"}):
                continue
            detected.append((match[0], label, match[1]))
            matched_labels.add(label)

    if not detected:
        fallback_points = _price_transition_key_points(text)
        detected = [
            (index, point, point)
            for index, point in enumerate(fallback_points)
            if point
        ]

    visual_duration = max(float(duration or 0), 1.0)
    latest_start = max(0.45, visual_duration - 0.6)
    previous_start = -0.5
    items: list[dict[str, Any]] = []
    for index, label, trigger_text in sorted(detected, key=lambda item: item[0])[:3]:
        raw_start = (index / text_length) * visual_duration
        start = max(0.45, min(raw_start, latest_start))
        if start <= previous_start:
            start = min(latest_start, previous_start + 0.55)
        previous_start = start
        timing = {
            "start": round(start, 3),
            "duration": round(max(0.8, visual_duration - start), 3),
        }
        items.append(
            {
                "label": label,
                "triggerText": trigger_text,
                "timing": timing,
            }
        )
    return items


def _build_price_transition_card(label: str, body: str, *, duration: float = 0.0) -> dict[str, Any]:
    items = _price_transition_parameter_items(body, duration)
    key_points = [safe_text(item.get("label")) for item in items if safe_text(item.get("label"))]
    if not key_points:
        key_points = _price_transition_key_points(body)
    return {
        "rangeLabel": safe_text(label),
        "headline": "重点参数",
        "keyPoints": key_points,
        "items": items,
        "visualEvents": [
            {
                "target": f"price_param_{index + 1:02d}",
                "text": item["label"],
                "trigger_text": item["triggerText"],
                "timing": item["timing"],
            }
            for index, item in enumerate(items)
        ],
        "audience": _price_transition_audience(body),
    }


def build_product_recommendation_package(
    db: Database,
    *,
    project_id: int,
    account_label: str,
    output_mode: str = "jianying_draft",
    product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
    mode: str = "standard",
    top_uids: list[str] | None = None,
    product_uids: list[str] | None = None,
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
    products = _ordered_products(
        repo.products(project_id, include_removed=False),
        mode=safe_text(mode) or "standard",
        top_uids=top_uids or [],
        product_uids=product_uids or [],
    )
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
    stale_product_images: list[dict[str, Any]] = []
    price_segments: dict[str, dict[str, Any]] = {}
    product_segments: dict[str, dict[str, Any]] = {}

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
        label = safe_text(block.get("price_range_label"))
        body = safe_text(block.get("body"))
        duration = get_audio_duration_seconds(voice_path)
        price_segments[label] = {
            "type": "price_transition",
            "id": f"price-{block.get('id')}",
            "priceRangeLabel": label,
            "transitionText": body,
            "priceTransitionCard": _build_price_transition_card(label, body, duration=duration),
            "voiceAsset": str(voice_path),
            "duration": duration,
            "sourceScriptBlockId": int(block.get("id") or 0),
        }
        if output_mode == "final_mp4":
            price_segments[label]["subtitles"] = _segment_subtitles(body, duration)

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

        image_path = _absolute_file_path(image.get("path")) if image else None
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
        if not image and (output_mode == "jianying_draft" or not product_card):
            product_missing = True
            missing.append(
                {
                    "kind": "product_image",
                    "uid": uid,
                    "title": title,
                    "message": "missing ready image for product",
                }
            )
        if product_missing:
            continue

        voice_path = _absolute_file_path(voice.get("path"))
        duration = get_audio_duration_seconds(voice_path)
        product_segment = {
            "type": "product_recommendation",
            "id": f"product-{uid}",
            "productUid": uid,
            "productTitle": title,
            "priceRangeLabel": safe_text(product.get("price_label")),
            "spokenText": safe_text(block.get("body")),
            "voiceAsset": str(voice_path),
            "imageCardAsset": str(image_path) if image_path else None,
            "videoAsset": str(video_path) if video_path else None,
            "productMediaMode": media_mode,
            "duration": duration,
            "sourceScriptBlockId": int(block.get("id") or 0),
            "assetBindingIds": {
                "image": int(image.get("id") or 0) if image else None,
                "voice": int(voice.get("id") or 0),
                "video": int(video.get("id") or 0) if video else None,
            },
            "subtitles": _segment_subtitles(safe_text(block.get("body")), duration)
            if output_mode == "final_mp4"
            else [],
        }
        if product_card:
            product_segment["productCard"] = product_card
            card_fingerprint = product_card_content_fingerprint(product, product_card)
            if card_fingerprint:
                product_segment["productCardFingerprint"] = card_fingerprint
                stored_image_fingerprint = safe_text(image.get("text_hash")) if image else ""
                if stored_image_fingerprint and stored_image_fingerprint != card_fingerprint:
                    stale_product_images.append(
                        {
                            "kind": "stale_product_image",
                            "uid": uid,
                            "title": title,
                            "asset_binding_id": int(image.get("id") or 0) if image else None,
                            "path": str(image_path) if image_path else "",
                            "stored_fingerprint": stored_image_fingerprint,
                            "expected_fingerprint": card_fingerprint,
                            "message": "product card image content fingerprint changed",
                        }
                    )
        elif video_path and image_path:
            display_template = display_template_from_image_path(str(image_path), account_label=account)
            if display_template:
                product_segment["displayTemplate"] = display_template
                product_segment["displayVideoSlot"] = _display_video_slot_for_template(display_template)
        product_segments[uid] = product_segment

    segments = _arrange_segments(
        products,
        price_blocks=price_blocks,
        price_segments=price_segments,
        product_segments=product_segments,
        mode=safe_text(mode) or "standard",
        top_uids=top_uids or [],
    )

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
    if output_mode == "final_mp4":
        package["output"]["subtitles"] = {
            "enabled": True,
            "styleId": _choose_subtitle_style_id(),
            "styleScope": "global",
        }
    return ProductRenderPackageResult(
        package=package,
        missing=missing,
        stale_product_images=stale_product_images,
    )


def _segment_subtitles(text: str, duration: float) -> list[dict[str, Any]]:
    return [
        {
            "start": round(start, 3),
            "end": round(end, 3),
            "text": safe_text(chunk),
        }
        for start, end, chunk in distribute_subtitle_text(
            safe_text(text),
            0.0,
            max(0.0, float(duration or 0.0)),
        )
    ]


def _choose_subtitle_style_id() -> str:
    return random.SystemRandom().choice(GLOBAL_SUBTITLE_STYLE_IDS)


def _display_video_slot_for_template(display_template: str) -> dict[str, Any]:
    slot = get_template_slot(display_template)
    slot.setdefault("sourceWidth", 1920)
    slot.setdefault("sourceHeight", 1080)
    return slot


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


def _ordered_products(
    products: list[dict[str, Any]],
    *,
    mode: str,
    top_uids: list[str],
    product_uids: list[str],
) -> list[dict[str, Any]]:
    selected = {uid.casefold() for uid in product_uids}
    if selected:
        products = [product for product in products if safe_text(product.get("uid")).casefold() in selected]
    if mode != "top" or not top_uids:
        return products
    rank = {uid.casefold(): index for index, uid in enumerate(top_uids)}
    return sorted(
        products,
        key=lambda product: (
            0,
            rank[safe_text(product.get("uid")).casefold()],
        )
        if safe_text(product.get("uid")).casefold() in rank
        else (1, int(product.get("sort_order") or 0)),
    )


def _arrange_segments(
    products: list[dict[str, Any]],
    *,
    price_blocks: list[dict[str, Any]],
    price_segments: dict[str, dict[str, Any]],
    product_segments: dict[str, dict[str, Any]],
    mode: str,
    top_uids: list[str],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    top_set = {uid.casefold() for uid in top_uids} if mode == "top" else set()
    used_price_labels: set[str] = set()

    for product in products:
        uid = safe_text(product.get("uid"))
        if uid.casefold() not in top_set:
            continue
        segment = product_segments.get(uid)
        if segment:
            segments.append(segment)

    for product in products:
        uid = safe_text(product.get("uid"))
        if uid.casefold() in top_set:
            continue
        segment = product_segments.get(uid)
        if not segment:
            continue
        price_label = _matching_price_label(product, price_blocks)
        if price_label and price_label not in used_price_labels:
            price_segment = price_segments.get(price_label)
            if price_segment:
                segments.append(price_segment)
                used_price_labels.add(price_label)
        segments.append(segment)

    return segments


def _matching_price_label(product: dict[str, Any], price_blocks: list[dict[str, Any]]) -> str:
    price = _first_number(safe_text(product.get("price_label")))
    if price is None:
        return ""
    for block in price_blocks:
        label = safe_text(block.get("price_range_label"))
        if _price_in_range(price, label):
            return label
    return ""


def _first_number(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _price_in_range(price: float, label: str) -> bool:
    try:
        numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", label)]
    except (ValueError, OverflowError):
        return False
    if not numbers:
        return False
    if len(numbers) == 1:
        if any(token in label for token in ("以上", "+", "up")):
            return price >= numbers[0]
        if any(token in label for token in ("以下", "以内", "under")):
            return price <= numbers[0]
        return abs(price - numbers[0]) < 0.001
    low, high = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
    return low <= price <= high


def _product_card_payload(
    product: dict[str, Any],
    *,
    project: dict[str, Any],
    fallback_image_path: Path | None,
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


def product_card_payload_for_product(
    product: dict[str, Any],
    *,
    project: dict[str, Any],
    fallback_image_path: str | Path | None = None,
) -> dict[str, Any] | None:
    return _product_card_payload(
        product,
        project=project,
        fallback_image_path=Path(fallback_image_path) if fallback_image_path else None,
    )


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


def product_card_content_fingerprint(product: dict[str, Any], product_card: dict[str, Any] | None) -> str:
    if not isinstance(product_card, dict):
        return ""
    data_map = product_card.get("dataMap")
    normalized_data_map = _string_map(data_map)
    if "cover" in normalized_data_map:
        normalized_data_map["cover"] = _cover_asset_identity(normalized_data_map["cover"])
    payload = {
        "version": "product-card-v1",
        "uid": safe_text(product.get("uid")),
        "title": safe_text(product.get("title")),
        "price": safe_text(product.get("price_label")),
        "templateId": safe_text(product_card.get("templateId")),
        "coverAsset": _cover_asset_identity(safe_text(product_card.get("coverAsset"))),
        "dataMap": normalized_data_map,
        "slots": _slot_list(product_card.get("slots")),
    }
    return text_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _cover_asset_identity(value: str) -> str:
    text = safe_text(value)
    if not text:
        return ""
    if _is_remote_url(text):
        parsed = urllib.parse.urlparse(text)
        return f"{parsed.netloc}/{Path(parsed.path).name}"
    return Path(text).name
