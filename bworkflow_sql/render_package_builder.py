from __future__ import annotations

import json
import math
import random
import re
import hashlib
import os
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from .db import Database
from .repositories import Repository
from .settings import (
    DEFAULT_INTRO_ASSET_ROOT,
    DEFAULT_RECOMMENDATION_BACKGROUND_ROOT,
    INTERNAL_WORKSPACE_ROOT,
)
from .subtitle_rules import normalize_subtitle_alignment_text
from .subtitle_helpers import (
    align_subtitle_jobs_with_asr_grouped,
    align_subtitle_text_with_asr,
    distribute_subtitle_text,
    probe_media_duration_seconds,
)
from .template_config import (
    get_remotion_template_metadata,
    get_template_slot,
    remotion_template_id_for_user,
    resolve_product_card_template,
)
from .tts_helpers import DEFAULT_LOUDNORM_I, DEFAULT_LOUDNORM_LRA, DEFAULT_LOUDNORM_TP
from .utils import safe_text, text_hash
from .price_transition_plan import (
    PRICE_TRANSITION_PLAN_VERSION,
    find_price_transition_plan_for_text,
    load_price_transition_plan_set,
    price_transition_card_from_plan,
    price_transition_plan_path,
)


SUPPORTED_OUTPUT_MODES = {"final_mp4"}
SUPPORTED_PRODUCT_MEDIA_MODES = {"cover_only", "video_preferred"}
DEFAULT_PRODUCT_MEDIA_MODE = "video_preferred"
SUPPORTED_PRODUCT_ORDER_STRATEGIES = {"price_segment_shuffle", "stable"}
DEFAULT_PRODUCT_ORDER_STRATEGY = "price_segment_shuffle"
SUPPORTED_SUBTITLE_ALIGNMENTS = {"proportional", "asr"}
GLOBAL_SUBTITLE_STYLE_IDS = (
    "classic_white",
    "impact_yellow",
    "panel_white",
    "warm_cream",
    "tech_cyan",
    "orange_energy",
)

PRICE_TRANSITION_SFX_FILES = {
    "titleHit": "sfx_title_hit.wav",
    "itemTick": "sfx_progress_tick.wav",
    "exitWhoosh": "sfx_transition_whoosh.wav",
}


def _price_transition_sound_effects(
    asset_root: str | Path = DEFAULT_INTRO_ASSET_ROOT,
) -> dict[str, str]:
    sfx_dir = Path(asset_root) / "1-音效"
    resolved = {role: sfx_dir / filename for role, filename in PRICE_TRANSITION_SFX_FILES.items()}
    missing = [path for path in resolved.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"price transition sound effect is missing: {missing[0]}")
    return {role: str(path) for role, path in resolved.items()}


def _product_motion_seed(project_id: int, account_label: str, product_uid: str) -> str:
    source = f"product-motion-v1|{project_id}|{safe_text(account_label)}|{safe_text(product_uid)}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
WHOLE_VIDEO_OUTRO_KEYWORD = "整片结尾"
WHOLE_VIDEO_OUTRO_COMMON_FOLDER = "1-通用"
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


class ProductCoverMaterializationError(ValueError):
    pass


def _dynamic_context_issue(uid: str, field: str, message: str) -> dict[str, str]:
    return {
        "kind": "dynamic_product_context",
        "uid": uid,
        "field": field,
        "message": message,
    }


def _validated_dynamic_context_map(
    products: list[dict[str, Any]],
    contexts: list[dict[str, Any]] | None,
    *,
    master_snapshot_id: str | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    expected_uids = [safe_text(product.get("uid")).strip() for product in products]
    expected = set(expected_uids)
    issues: list[dict[str, Any]] = []
    if not isinstance(master_snapshot_id, str) or not master_snapshot_id.strip():
        issues.append(
            _dynamic_context_issue(
                "",
                "master_snapshot_id",
                "final_mp4 requires the frozen Master snapshot id as a non-empty string",
            )
        )
    if not isinstance(contexts, list):
        contexts = []
        issues.append(
            _dynamic_context_issue(
                "",
                "contexts",
                "final_mp4 requires frozen dynamic product contexts as a list",
            )
        )

    result: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    seen_uids: set[str] = set()
    for context_index, context in enumerate(contexts):
        if not isinstance(context, dict):
            issues.append(
                _dynamic_context_issue(
                    "",
                    f"contexts[{context_index}]",
                    "dynamic product context must be an object",
                )
            )
            continue

        raw_uid = context.get("product_uid")
        uid = raw_uid.strip() if isinstance(raw_uid, str) else ""
        if not isinstance(raw_uid, str) or not uid:
            issues.append(
                _dynamic_context_issue(
                    "",
                    "product_uid",
                    "dynamic product context product_uid must be a non-empty string",
                )
            )
        elif uid in seen_uids:
            duplicates.add(uid)
        else:
            seen_uids.add(uid)

        normalized_data: dict[str, str] = {}
        raw_data = context.get("data_map")
        if not isinstance(raw_data, dict):
            issues.append(
                _dynamic_context_issue(uid, "data_map", "data_map must be an object")
            )
        else:
            for field in (
                "title",
                "displayPrice",
                "review",
                "priceBandLabel",
                "categoryLabel",
            ):
                value = raw_data.get(field)
                if not isinstance(value, str):
                    issues.append(
                        _dynamic_context_issue(
                            uid,
                            f"data_map.{field}",
                            f"data_map.{field} must be a string",
                        )
                    )
                    continue
                normalized_value = value.strip()
                if field in {"title", "displayPrice", "priceBandLabel"} and not normalized_value:
                    issues.append(
                        _dynamic_context_issue(
                            uid,
                            f"data_map.{field}",
                            f"data_map.{field} must be non-empty",
                        )
                    )
                    continue
                normalized_data[field] = normalized_value

        normalized_specs: list[dict[str, str]] = []
        raw_specs = context.get("specs")
        if not isinstance(raw_specs, list):
            issues.append(
                _dynamic_context_issue(uid, "specs", "specs must be a list")
            )
        else:
            for spec_index, raw_spec in enumerate(raw_specs):
                if not isinstance(raw_spec, dict):
                    issues.append(
                        _dynamic_context_issue(
                            uid,
                            f"specs[{spec_index}]",
                            f"specs[{spec_index}] must be an object",
                        )
                    )
                    continue
                normalized_spec: dict[str, str] = {}
                for field in ("label", "value"):
                    value = raw_spec.get(field)
                    if not isinstance(value, str) or not value.strip():
                        issues.append(
                            _dynamic_context_issue(
                                uid,
                                f"specs[{spec_index}].{field}",
                                f"specs[{spec_index}].{field} must be a non-empty string",
                            )
                        )
                        continue
                    normalized_spec[field] = value.strip()
                if set(normalized_spec) == {"label", "value"}:
                    normalized_specs.append(normalized_spec)

        raw_media_kind = context.get("media_kind")
        media_kind = raw_media_kind.strip() if isinstance(raw_media_kind, str) else ""
        if not isinstance(raw_media_kind, str) or media_kind not in {"cover", "video"}:
            issues.append(
                _dynamic_context_issue(
                    uid,
                    "media_kind",
                    "media_kind must be either cover or video",
                )
            )

        raw_media_asset = context.get("media_asset")
        media_asset = raw_media_asset.strip() if isinstance(raw_media_asset, str) else ""
        normalized_media_asset = media_asset
        if not isinstance(raw_media_asset, str) or not media_asset:
            issues.append(
                _dynamic_context_issue(
                    uid,
                    "media_asset",
                    "media_asset must be a non-empty string",
                )
            )
        elif _is_remote_url(media_asset):
            if media_kind == "video":
                issues.append(
                    _dynamic_context_issue(
                        uid,
                        "media_asset",
                        "video media_asset must be an existing local file",
                    )
                )
        else:
            try:
                media_path = Path(media_asset).resolve()
                normalized_media_asset = str(media_path)
                media_exists = media_path.is_file()
            except (OSError, RuntimeError, ValueError) as exc:
                media_exists = False
                normalized_media_asset = media_asset
                media_error = str(exc)
            else:
                media_error = ""
            if not media_exists:
                issues.append(
                    _dynamic_context_issue(
                        uid,
                        "media_asset",
                        f"media_asset does not exist: {media_asset}"
                        + (f": {media_error}" if media_error else ""),
                    )
                )

        raw_voice_asset = context.get("voice_asset")
        voice_asset = raw_voice_asset.strip() if isinstance(raw_voice_asset, str) else ""
        normalized_voice_asset = voice_asset
        voice_duration: float | None = None
        if not isinstance(raw_voice_asset, str) or not voice_asset:
            issues.append(
                _dynamic_context_issue(
                    uid,
                    "voice_asset",
                    "voice_asset must be a non-empty string",
                )
            )
        elif _is_remote_url(voice_asset):
            issues.append(
                _dynamic_context_issue(
                    uid,
                    "voice_asset",
                    "voice_asset must be an existing local file",
                )
                )
        else:
            try:
                voice_path = Path(voice_asset).resolve()
                normalized_voice_asset = str(voice_path)
                voice_exists = voice_path.is_file()
            except (OSError, RuntimeError, ValueError) as exc:
                voice_exists = False
                normalized_voice_asset = voice_asset
                voice_error = str(exc)
            else:
                voice_error = ""
            if not voice_exists:
                issues.append(
                    _dynamic_context_issue(
                        uid,
                        "voice_asset",
                        f"voice_asset does not exist: {voice_asset}"
                        + (f": {voice_error}" if voice_error else ""),
                    )
                )
            else:
                try:
                    voice_duration = float(get_audio_duration_seconds(voice_path))
                    if not math.isfinite(voice_duration) or voice_duration <= 0:
                        raise ValueError("duration must be positive")
                except Exception as exc:
                    voice_duration = None
                    issues.append(
                        _dynamic_context_issue(
                            uid,
                            "voice_asset",
                            f"voice_asset duration could not be read: {exc}",
                        )
                    )

        raw_spoken_text = context.get("spoken_text")
        spoken_text = raw_spoken_text.strip() if isinstance(raw_spoken_text, str) else ""
        if not isinstance(raw_spoken_text, str) or not spoken_text:
            issues.append(
                _dynamic_context_issue(
                    uid,
                    "spoken_text",
                    "spoken_text must be a non-empty string",
                )
            )

        source_script_block_id = context.get("source_script_block_id")
        if type(source_script_block_id) is not int or source_script_block_id <= 0:
            issues.append(
                _dynamic_context_issue(
                    uid,
                    "source_script_block_id",
                    "source_script_block_id must be a positive integer",
                )
            )

        if uid and uid not in result:
            result[uid] = {
                "product_uid": uid,
                "data_map": normalized_data,
                "specs": normalized_specs,
                "media_kind": media_kind,
                "media_asset": normalized_media_asset,
                "voice_asset": normalized_voice_asset,
                "spoken_text": spoken_text,
                "source_script_block_id": source_script_block_id,
                "_voice_duration": voice_duration,
            }
    for uid in sorted(duplicates):
        issues.append(
            _dynamic_context_issue(
                uid,
                "product_uid",
                "duplicate dynamic product context",
            )
        )
        result.pop(uid, None)
    for uid in sorted(set(result) - expected):
        issues.append(
            _dynamic_context_issue(
                uid,
                "product_uid",
                "dynamic product context does not match a selected product",
            )
        )
        result.pop(uid, None)
    for uid in expected_uids:
        if uid not in seen_uids:
            issues.append(
                _dynamic_context_issue(
                    uid,
                    "product_uid",
                    "missing dynamic product context for selected product",
                )
            )
    if issues:
        return {}, issues
    return result, []


def _dynamic_product_segment(
    context: dict[str, Any],
    *,
    project: dict[str, Any],
    selected_template: dict[str, Any],
    subtitle_alignment: str,
) -> dict[str, Any]:
    uid = context["product_uid"]
    semantic_data = context["data_map"]
    specs = context["specs"]
    media_kind = context["media_kind"]
    media_value = context["media_asset"]
    if media_kind == "cover" and _is_remote_url(media_value):
        media_path = _ensure_remote_cover_cached(
            media_value,
            category=safe_text(project.get("category_name") or project.get("name")),
            uid=uid,
        )
    else:
        media_path = Path(media_value)

    voice_path = Path(context["voice_asset"])
    spoken_text = context["spoken_text"]
    source_script_block_id = context["source_script_block_id"]

    template_id = safe_text(selected_template.get("templateId"))
    template_version = safe_text(selected_template.get("templateVersion"))
    if not template_id or not template_version:
        raise ValueError("selected product-card template metadata is incomplete")
    product_card: dict[str, Any] = {
        "templateId": template_id,
        "templateVersion": template_version,
        "dataMap": semantic_data,
        "slots": specs,
    }
    if media_kind == "cover":
        product_card["coverAsset"] = str(media_path)
    for metadata_key in (
        "cardPlacement",
        "outputCanvas",
        "coverMediaSlot",
        "videoOverlaySlot",
    ):
        metadata_value = selected_template.get(metadata_key)
        if isinstance(metadata_value, dict):
            product_card[metadata_key] = dict(metadata_value)

    duration = context["_voice_duration"]
    segment: dict[str, Any] = {
        "type": "product_recommendation",
        "id": f"product-{uid}",
        "productUid": uid,
        "productTitle": semantic_data["title"],
        "priceRangeLabel": semantic_data["priceBandLabel"],
        "spokenText": spoken_text,
        "voiceAsset": str(voice_path),
        "productMediaMode": "video_preferred",
        "duration": duration,
        "sourceScriptBlockId": source_script_block_id,
        "productCard": product_card,
        "subtitles": (
            []
            if subtitle_alignment == "asr"
            else _segment_subtitles(
                spoken_text,
                duration,
                subtitle_alignment=subtitle_alignment,
            )
        ),
    }
    if media_kind == "video":
        segment["videoAsset"] = str(media_path)
    return segment


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
    output_mode: str = "final_mp4",
    product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
    product_order_strategy: str = DEFAULT_PRODUCT_ORDER_STRATEGY,
    product_card_template_id: str = "",
    mode: str = "standard",
    top_uids: list[str] | None = None,
    product_uids: list[str] | None = None,
    subtitle_alignment: str = "proportional",
    intro_video_path: str | Path | None = None,
    intro_video_text: str = "",
    include_outro: bool = False,
    closing_text: str = "",
    dynamic_product_contexts: list[dict[str, Any]] | None = None,
    master_snapshot_id: str | None = None,
) -> ProductRenderPackageResult:
    if output_mode not in SUPPORTED_OUTPUT_MODES:
        raise ValueError(f"unsupported output_mode: {output_mode}")
    media_mode = safe_text(product_media_mode) or DEFAULT_PRODUCT_MEDIA_MODE
    if media_mode not in SUPPORTED_PRODUCT_MEDIA_MODES:
        raise ValueError(f"unsupported product_media_mode: {media_mode}")
    if output_mode == "final_mp4":
        media_mode = "video_preferred"
    order_strategy = safe_text(product_order_strategy) or DEFAULT_PRODUCT_ORDER_STRATEGY
    if order_strategy not in SUPPORTED_PRODUCT_ORDER_STRATEGIES:
        raise ValueError(f"unsupported product_order_strategy: {order_strategy}")
    subtitle_mode = safe_text(subtitle_alignment) or "proportional"
    if subtitle_mode not in SUPPORTED_SUBTITLE_ALIGNMENTS:
        raise ValueError(f"unsupported subtitle_alignment: {subtitle_mode}")
    explicit_template_requested = bool(safe_text(product_card_template_id))
    selected_template = resolve_product_card_template(
        account_label,
        product_card_template_id,
    )
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
    price_blocks = [
        block
        for block in blocks
        if safe_text(block.get("script_type")) == "price_transition"
    ]

    missing: list[dict[str, Any]] = []
    stale_product_images: list[dict[str, Any]] = []
    price_segments: dict[str, dict[str, Any]] = {}
    product_segments: dict[str, dict[str, Any]] = {}
    dynamic_context_by_uid: dict[str, dict[str, Any]] = {}
    if output_mode == "final_mp4":
        dynamic_context_by_uid, context_issues = _validated_dynamic_context_map(
            products,
            dynamic_product_contexts,
            master_snapshot_id=master_snapshot_id,
        )
        missing.extend(context_issues)
    price_plan_error = ""
    try:
        strict_price_plan = load_price_transition_plan_set(project_id) is not None
    except ValueError as exc:
        strict_price_plan = True
        price_plan_error = str(exc)

    for block in price_blocks:
        label = safe_text(block.get("price_range_label"))
        body = safe_text(block.get("body"))
        block_label = safe_text(block.get("block_label")) or "正文"
        matched_price_plan = None
        if strict_price_plan and not price_plan_error:
            matched_price_plan = find_price_transition_plan_for_text(
                project_id,
                price_range_label=label,
                block_label=block_label,
                body=body,
            )
        if strict_price_plan and (price_plan_error or matched_price_plan is None):
            missing.append(
                {
                    "kind": "price_transition_plan",
                    "price_range_label": label,
                    "block_label": block_label,
                    "path": str(price_transition_plan_path(project_id)),
                    "message": price_plan_error
                    or "price transition text does not match the structured source plan",
                }
            )
            continue
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
        duration = get_audio_duration_seconds(voice_path)
        price_card = (
            price_transition_card_from_plan(matched_price_plan, duration=duration)
            if matched_price_plan is not None
            else _build_price_transition_card(label, body, duration=duration)
        )
        price_segments[label] = {
            "type": "price_transition",
            "id": f"price-{block.get('id')}",
            "priceRangeLabel": label,
            "transitionText": body,
            "priceTransitionCard": price_card,
            "voiceAsset": str(voice_path),
            "soundEffects": _price_transition_sound_effects(),
            "duration": duration,
            "sourceScriptBlockId": int(block.get("id") or 0),
        }
        if matched_price_plan is not None:
            price_segments[label]["priceTransitionPlanVersion"] = PRICE_TRANSITION_PLAN_VERSION
        if output_mode == "final_mp4":
            price_segments[label]["subtitles"] = (
                []
                if subtitle_mode == "asr"
                else _segment_subtitles(body, duration, subtitle_alignment=subtitle_mode)
            )

    for product in products:
        uid = safe_text(product.get("uid"))
        title = safe_text(product.get("title"))
        if output_mode == "final_mp4":
            context = dynamic_context_by_uid.get(uid)
            if context is None:
                continue
            try:
                product_segments[uid] = _dynamic_product_segment(
                    context,
                    project=project,
                    selected_template=selected_template,
                    subtitle_alignment=subtitle_mode,
                )
            except ValueError as exc:
                missing.append(
                    {
                        "kind": (
                            "product_cover"
                            if isinstance(exc, ProductCoverMaterializationError)
                            else "dynamic_product_context"
                        ),
                        "uid": uid,
                        "message": str(exc),
                    }
                )
            continue
    segments = _arrange_segments(
        [
            {
                **product,
                "_dynamic_price_band_label": safe_text(
                    (
                        dynamic_context_by_uid.get(safe_text(product.get("uid")), {}).get(
                            "data_map", {}
                        )
                        if isinstance(
                            dynamic_context_by_uid.get(safe_text(product.get("uid")), {}).get(
                                "data_map"
                            ),
                            dict,
                        )
                        else {}
                    ).get("priceBandLabel")
                )
                or product.get("price_label"),
            }
            for product in products
        ]
        if output_mode == "final_mp4"
        else products,
        price_blocks=price_blocks,
        price_segments=price_segments,
        product_segments=product_segments,
        mode=safe_text(mode) or "standard",
        top_uids=top_uids or [],
        product_order_strategy=order_strategy,
    )

    if output_mode == "final_mp4" and intro_video_path:
        intro_path = _absolute_file_path(intro_video_path)
        intro_text = safe_text(intro_video_text).strip()
        if not intro_path.is_file():
            missing.append(
                {"kind": "intro_video", "path": str(intro_path), "message": "intro video does not exist"}
            )
        elif not intro_text:
            missing.append(
                {"kind": "intro_text", "path": str(intro_path), "message": "intro transcript text is required"}
            )
        else:
            intro_duration = probe_media_duration_seconds(intro_path)
            intro_segment = {
                "type": "intro",
                "id": "intro-raw",
                "spokenText": intro_text,
                "videoAsset": str(intro_path),
                "duration": intro_duration,
                "subtitles": (
                    []
                    if subtitle_mode == "asr"
                    else _segment_subtitles(intro_text, intro_duration, subtitle_alignment=subtitle_mode)
                ),
            }
            segments.insert(0, intro_segment)

    if output_mode == "final_mp4" and include_outro:
        account_record = next(
            (item for item in repo.accounts() if safe_text(item.get("label")) == account),
            None,
        )
        closing_audio = _absolute_file_path(account_record.get("closing_audio_path")) if account_record else None
        outro_text = safe_text(closing_text).strip()
        if closing_audio is None or not closing_audio.is_file():
            missing.append(
                {"kind": "closing_audio", "account": account, "message": "account closing audio does not exist"}
            )
        elif not outro_text:
            missing.append(
                {"kind": "closing_text", "account": account, "message": "closing text is required"}
            )
        else:
            outro_video, outro_seed = _select_whole_video_outro(
                project_id=project_id,
                account=account,
                closing_text=outro_text,
                segment_ids=[safe_text(item.get("id")) for item in segments],
            )
            outro_duration = probe_media_duration_seconds(outro_video)
            closing_duration = get_audio_duration_seconds(closing_audio)
            if closing_duration > outro_duration + 0.05:
                missing.append(
                    {
                        "kind": "closing_video_too_short",
                        "account": account,
                        "video": str(outro_video),
                        "video_duration": outro_duration,
                        "voice_duration": closing_duration,
                        "message": "whole-video outro MP4 is shorter than account closing voice",
                    }
                )
                outro_video = None
            if outro_video is None:
                pass
            else:
                segments.append(
                    {
                        "type": "outro",
                        "id": "outro-fixed",
                        "spokenText": outro_text,
                        "voiceAsset": str(closing_audio),
                        "videoAsset": str(outro_video),
                        "duration": outro_duration,
                        "seed": outro_seed,
                        "selectionKeyword": WHOLE_VIDEO_OUTRO_KEYWORD,
                        "subtitles": (
                            []
                            if subtitle_mode == "asr"
                            else _segment_subtitles(outro_text, closing_duration, subtitle_alignment=subtitle_mode)
                        ),
                    }
                )

    if output_mode == "final_mp4" and subtitle_mode == "asr" and not missing:
        _align_package_segments_with_forced_alignment(segments)

    package = {
        "schemaVersion": "1.0.0",
        "packageType": "bilibili_video",
        "project": {
            "category": safe_text(project.get("category_name") or project.get("name")),
            "account": account,
            "bworkflowProjectId": int(project_id),
            "masterSchemeId": safe_text(project.get("scheme_id")),
            **(
                {"masterSnapshotId": safe_text(master_snapshot_id)}
                if output_mode == "final_mp4" and safe_text(master_snapshot_id)
                else {}
            ),
        },
        "output": {
            "mode": output_mode,
            "productMediaMode": media_mode,
            "productOrderStrategy": order_strategy,
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
        "assets": (
            {
                "recommendationBackgroundCandidates": [str(DEFAULT_RECOMMENDATION_BACKGROUND_ROOT)],
                "assetFallbackPolicy": "forbid",
            }
            if output_mode == "final_mp4"
            else {}
        ),
        "approval": {
            "productRecommendationBatch": {
                "status": "pending",
                "reviewedAt": None,
            }
        },
    }
    if selected_template:
        template_id = safe_text(selected_template.get("templateId"))
        template_version = safe_text(selected_template.get("templateVersion"))
        package["output"]["productCardTemplateId"] = template_id
        package["output"]["productCardTemplateVersion"] = template_version
        package["output"]["productCardTemplate"] = {
            "id": template_id,
            "displayName": safe_text(selected_template.get("displayName")),
            "version": template_version,
            "confirmed": explicit_template_requested,
            "selectionSource": "explicit"
            if explicit_template_requested
            else "account_default_compat",
        }
    if output_mode == "final_mp4":
        package["output"]["subtitles"] = {
            "enabled": True,
            "styleId": _choose_subtitle_style_id(package),
            "styleScope": "global",
            "alignment": subtitle_mode,
        }
    return ProductRenderPackageResult(
        package=package,
        missing=missing,
        stale_product_images=stale_product_images,
    )


def _select_whole_video_outro(
    *,
    project_id: int,
    account: str,
    closing_text: str,
    segment_ids: list[str],
) -> tuple[Path, str]:
    common_dir = Path(DEFAULT_INTRO_ASSET_ROOT) / WHOLE_VIDEO_OUTRO_COMMON_FOLDER
    candidates = sorted(
        path.resolve()
        for path in common_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".mp4"
        and WHOLE_VIDEO_OUTRO_KEYWORD in path.stem
    ) if common_dir.is_dir() else []
    if not candidates:
        raise FileNotFoundError(
            f"whole-video outro asset is missing: {common_dir} has no MP4 containing {WHOLE_VIDEO_OUTRO_KEYWORD}"
        )
    seed_source = "|".join(
        [
            "whole-video-outro-v1",
            str(project_id),
            safe_text(account),
            text_hash(closing_text),
            ",".join(segment_ids),
        ]
    )
    seed = hashlib.sha256(seed_source.encode("utf-8")).hexdigest()[:24]
    return random.Random(seed).choice(candidates), seed


def _align_package_segments_with_forced_alignment(segments: list[dict[str, Any]]) -> None:
    jobs: list[dict[str, Any]] = []
    aligned_segments: list[dict[str, Any]] = []
    for segment in segments:
        segment_type = safe_text(segment.get("type"))
        if segment_type == "price_transition":
            text = safe_text(segment.get("transitionText"))
        else:
            text = safe_text(segment.get("spokenText"))
        audio_path = safe_text(segment.get("videoAsset" if segment_type == "intro" else "voiceAsset"))
        if segment_type not in {"intro", "price_transition", "product_recommendation", "outro"}:
            continue
        if not text or not audio_path:
            raise ValueError(f"{segment.get('id') or segment_type} 强制对齐缺少精确原文或音频")
        jobs.append(
            {
                "label": safe_text(segment.get("id")) or segment_type,
                "audio_path": audio_path,
                "text": text,
                "offset_sec": 0.0,
            }
        )
        aligned_segments.append(segment)

    grouped = align_subtitle_jobs_with_asr_grouped(jobs)
    if len(grouped) != len(aligned_segments):
        raise ValueError(
            f"强制对齐返回 {len(grouped)} 组，但渲染段共有 {len(aligned_segments)} 组"
        )
    for segment, items in zip(aligned_segments, grouped):
        segment["subtitles"] = [
            {"start": round(start, 3), "end": round(end, 3), "text": safe_text(text)}
            for start, end, text in items
        ]
        if safe_text(segment.get("type")) == "price_transition" and safe_text(
            segment.get("priceTransitionPlanVersion")
        ):
            _align_price_transition_card_with_subtitles(segment)


def _align_price_transition_card_with_subtitles(segment: dict[str, Any]) -> None:
    card = segment.get("priceTransitionCard")
    if not isinstance(card, dict):
        raise ValueError("structured price transition is missing priceTransitionCard")
    items = [item for item in card.get("items") or [] if isinstance(item, dict)]
    if not 2 <= len(items) <= 3:
        raise ValueError("structured price transition must contain 2 to 3 card items")

    body = normalize_subtitle_alignment_text(safe_text(segment.get("transitionText")))
    subtitles = [item for item in segment.get("subtitles") or [] if isinstance(item, dict)]
    chunks: list[tuple[int, int, dict[str, Any]]] = []
    cursor = 0
    for subtitle in subtitles:
        text = normalize_subtitle_alignment_text(safe_text(subtitle.get("text")))
        if not text:
            continue
        chunks.append((cursor, cursor + len(text), subtitle))
        cursor += len(text)
    if not body or not chunks:
        raise ValueError("structured price transition forced alignment is missing body or subtitles")

    search_cursor = 0
    previous_start = -0.5
    duration = max(float(segment.get("duration") or 0), 1.0)
    for item in items:
        trigger_text = safe_text(item.get("triggerText") or item.get("trigger_text"))
        trigger = normalize_subtitle_alignment_text(trigger_text)
        position = body.find(trigger, search_cursor)
        if position < 0:
            raise ValueError(f"structured price transition trigger is missing from body: {trigger_text}")
        search_cursor = position + len(trigger)
        chunk = next((entry for entry in chunks if entry[0] <= position < entry[1]), None)
        if chunk is None:
            raise ValueError(f"structured price transition trigger has no forced subtitle anchor: {trigger_text}")
        chunk_start, chunk_end, subtitle = chunk
        subtitle_start = float(subtitle.get("start") or 0.0)
        subtitle_end = float(subtitle.get("end") or subtitle_start + 0.1)
        ratio = (position - chunk_start) / max(chunk_end - chunk_start, 1)
        start = subtitle_start + max(0.0, min(ratio, 1.0)) * max(subtitle_end - subtitle_start, 0.1)
        start = max(0.45, min(start, max(0.45, duration - 0.6)))
        if start <= previous_start:
            start = min(max(0.45, duration - 0.6), previous_start + 0.15)
        previous_start = start
        item["timing"] = {
            "start": round(start, 3),
            "duration": round(max(0.8, duration - start), 3),
        }
    card["keyPoints"] = [safe_text(item.get("label")) for item in items]
    card["visualEvents"] = [
        {
            "target": f"price_param_{index + 1:02d}",
            "text": safe_text(item.get("label")),
            "trigger_text": safe_text(item.get("triggerText") or item.get("trigger_text")),
            "timing": item["timing"],
        }
        for index, item in enumerate(items)
    ]


def _segment_subtitles(
    text: str,
    duration: float,
    *,
    audio_path: Path | None = None,
    subtitle_alignment: str = "proportional",
) -> list[dict[str, Any]]:
    mode = safe_text(subtitle_alignment) or "proportional"
    if mode not in SUPPORTED_SUBTITLE_ALIGNMENTS:
        raise ValueError(f"unsupported subtitle_alignment: {mode}")
    if mode == "asr":
        if audio_path is None:
            raise ValueError("精确原文强制对齐需要 audio_path")
        aligned = align_subtitle_text_with_asr(audio_path, safe_text(text), 0.0)
        return [
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "text": safe_text(chunk),
            }
            for start, end, chunk in aligned
        ]
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


def _choose_subtitle_style_id(package: dict[str, Any] | None = None) -> str:
    if not package:
        return random.SystemRandom().choice(GLOBAL_SUBTITLE_STYLE_IDS)
    project = package.get("project") if isinstance(package.get("project"), dict) else {}
    output = package.get("output") if isinstance(package.get("output"), dict) else {}
    seed_parts = [
        safe_text(project.get("id")),
        safe_text(project.get("category")),
        safe_text(project.get("account")),
        safe_text(output.get("productCardTemplateId")),
        safe_text(output.get("productMediaMode")),
        safe_text(output.get("productOrderStrategy")),
    ]
    seed = "|".join(seed_parts)
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return GLOBAL_SUBTITLE_STYLE_IDS[int(digest[:8], 16) % len(GLOBAL_SUBTITLE_STYLE_IDS)]


def _shuffle_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shuffled = list(products)
    random.SystemRandom().shuffle(shuffled)
    return shuffled


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
    preferred_image_set: str = "",
) -> dict[str, Any] | None:
    preferred_set = safe_text(preferred_image_set)
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
            (
                not _image_path_uses_template_set(
                    Path(safe_text(item.get("path"))),
                    image_set=preferred_set,
                )
                if asset_type == "image" and preferred_set
                else False
            ),
            safe_text(item.get("account_label")) != account_label,
            safe_text(item.get("path")),
        ),
    )[0]


def _image_path_uses_template_set(path: Path | None, *, image_set: str) -> bool:
    if path is None:
        return False
    template = safe_text(image_set)
    return any(safe_text(part) == template for part in path.parts)


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
    product_order_strategy: str,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    top_set = {uid.casefold() for uid in top_uids} if mode == "top" else set()

    for product in products:
        uid = safe_text(product.get("uid"))
        if uid.casefold() not in top_set:
            continue
        segment = product_segments.get(uid)
        if segment:
            segments.append(segment)

    remaining_products = [
        product
        for product in products
        if safe_text(product.get("uid")).casefold() not in top_set
        and product_segments.get(safe_text(product.get("uid")))
    ]
    if product_order_strategy == "price_segment_shuffle" and _has_matching_price_groups(
        remaining_products,
        price_blocks,
    ):
        segments.extend(
            _arrange_price_segment_shuffle(
                remaining_products,
                price_blocks=price_blocks,
                price_segments=price_segments,
                product_segments=product_segments,
            )
        )
        return segments

    used_price_labels: set[str] = set()
    segments.extend(
        _arrange_stable_price_segments(
            remaining_products,
            price_blocks=price_blocks,
            price_segments=price_segments,
            product_segments=product_segments,
            used_price_labels=used_price_labels,
        )
    )
    return segments


def _arrange_stable_price_segments(
    products: list[dict[str, Any]],
    *,
    price_blocks: list[dict[str, Any]],
    price_segments: dict[str, dict[str, Any]],
    product_segments: dict[str, dict[str, Any]],
    used_price_labels: set[str] | None = None,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    labels = used_price_labels if used_price_labels is not None else set()
    for product in products:
        uid = safe_text(product.get("uid"))
        segment = product_segments.get(uid)
        if not segment:
            continue
        price_label = _matching_price_label(product, price_blocks)
        if price_label and price_label not in labels:
            price_segment = price_segments.get(price_label)
            if price_segment:
                segments.append(price_segment)
                labels.add(price_label)
        segments.append(segment)
    return segments


def _arrange_price_segment_shuffle(
    products: list[dict[str, Any]],
    *,
    price_blocks: list[dict[str, Any]],
    price_segments: dict[str, dict[str, Any]],
    product_segments: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    ordered_labels: list[str] = []
    unmatched: list[dict[str, Any]] = []
    for product in products:
        price_label = _matching_price_label(product, price_blocks)
        if price_label:
            if price_label not in grouped:
                ordered_labels.append(price_label)
            grouped.setdefault(price_label, []).append(product)
        else:
            unmatched.append(product)

    used_price_labels: set[str] = set()
    for label in ordered_labels:
        group = grouped.get(label, [])
        if not group:
            continue
        price_segment = price_segments.get(label)
        if price_segment:
            segments.append(price_segment)
            used_price_labels.add(label)
        for product in _shuffle_products(group):
            segment = product_segments.get(safe_text(product.get("uid")))
            if segment:
                segments.append(segment)

    if unmatched:
        segments.extend(
            _arrange_stable_price_segments(
                unmatched,
                price_blocks=price_blocks,
                price_segments=price_segments,
                product_segments=product_segments,
                used_price_labels=used_price_labels,
            )
        )
    return segments


def _has_matching_price_groups(products: list[dict[str, Any]], price_blocks: list[dict[str, Any]]) -> bool:
    if not price_blocks:
        return False
    return any(_matching_price_label(product, price_blocks) for product in products)


def _matching_price_label(product: dict[str, Any], price_blocks: list[dict[str, Any]]) -> str:
    dynamic_label = safe_text(product.get("_dynamic_price_band_label"))
    if dynamic_label:
        return dynamic_label
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
    account_label: str = "",
    product_card_template_id: str = "",
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

    payload_template_id = safe_text(payload.get("templateId")) or "xiaoran1"
    selected_template = resolve_product_card_template(account_label, product_card_template_id)
    account_template_id = safe_text(selected_template.get("templateId")) or remotion_template_id_for_user(account_label)
    template_id = account_template_id or payload_template_id
    template_version = safe_text(payload.get("templateVersion"))
    if account_template_id and account_template_id != payload_template_id:
        template_version = ""
    cover_media_slot: dict[str, Any] = {
        "x": 24,
        "y": 140,
        "width": 507,
        "height": 318,
        "sourceWidth": 970,
        "sourceHeight": 480,
    }
    video_overlay_slot: Any = None
    try:
        remotion_metadata = selected_template or get_remotion_template_metadata(template_id)
    except ValueError:
        remotion_metadata = {}
    if remotion_metadata:
        template_version = template_version or safe_text(remotion_metadata.get("templateVersion"))
        metadata_slot = remotion_metadata.get("coverMediaSlot")
        if isinstance(metadata_slot, dict):
            cover_media_slot = dict(metadata_slot)
        video_overlay_slot = remotion_metadata.get("videoOverlaySlot")

    normalized: dict[str, Any] = {
        "templateId": template_id,
        "templateVersion": template_version,
        "dataMap": _string_map(data_map),
        "slots": _slot_list(slots),
        "coverMediaSlot": cover_media_slot,
    }
    if isinstance(video_overlay_slot, dict):
        normalized["videoOverlaySlot"] = dict(video_overlay_slot)
    for metadata_key in ("cardPlacement", "outputCanvas"):
        metadata_value = remotion_metadata.get(metadata_key)
        if isinstance(metadata_value, dict):
            normalized[metadata_key] = dict(metadata_value)
    if cover_asset:
        normalized["coverAsset"] = cover_asset
        normalized["dataMap"]["cover"] = cover_asset
    return normalized


def product_card_payload_for_product(
    product: dict[str, Any],
    *,
    project: dict[str, Any],
    fallback_image_path: str | Path | None = None,
    account_label: str = "",
    product_card_template_id: str = "",
) -> dict[str, Any] | None:
    return _product_card_payload(
        product,
        project=project,
        fallback_image_path=Path(fallback_image_path) if fallback_image_path else None,
        account_label=account_label,
        product_card_template_id=product_card_template_id,
    )


def _is_remote_url(value: str) -> bool:
    text = safe_text(value).lower()
    return text.startswith("http://") or text.startswith("https://")


def _ensure_remote_cover_cached(url: str, *, category: str, uid: str) -> Path:
    target = _cover_cache_path(category=category, uid=uid, url=url)
    png_target = target.with_suffix(".png")
    webp_target = target.with_suffix(".webp")
    cache_candidates = tuple(
        dict.fromkeys(
            (
                png_target,
                target,
                target.with_suffix(".jpg"),
                target.with_suffix(".jpeg"),
                webp_target,
            )
        )
    )
    for cached_path in cache_candidates:
        if not cached_path.is_file():
            continue
        try:
            cached_stat = cached_path.stat()
            cached_data = cached_path.read_bytes()
        except OSError as exc:
            raise ProductCoverMaterializationError(
                f"failed to read cached product cover for {uid}: {url}: {exc}"
            ) from exc
        try:
            suffix, materialized = _validated_cover_payload(cached_data)
        except Exception:
            try:
                _unlink_corrupt_cover_if_unchanged(
                    cached_path,
                    expected_data=cached_data,
                    expected_stat=cached_stat,
                )
            except OSError as exc:
                raise ProductCoverMaterializationError(
                    f"failed to remove corrupt product cover for {uid}: {url}: {exc}"
                ) from exc
            continue
        try:
            return _materialize_validated_cover(
                suffix,
                materialized,
                target=cached_path,
            )
        except Exception as exc:
            raise ProductCoverMaterializationError(
                f"failed to cache product cover for {uid}: {url}: {exc}"
            ) from exc

    try:
        data = _download_url_bytes(url)
        return _materialize_cover_bytes(data, target=target)
    except Exception as exc:
        raise ProductCoverMaterializationError(
            f"failed to cache product cover for {uid}: {url}: {exc}"
        ) from exc


def _materialize_cover_bytes(data: bytes, *, target: Path) -> Path:
    suffix, materialized = _validated_cover_payload(data)
    return _materialize_validated_cover(suffix, materialized, target=target)


def _materialize_validated_cover(suffix: str, data: bytes, *, target: Path) -> Path:
    resolved_target = target.with_suffix(suffix)
    if resolved_target.is_file() and resolved_target.read_bytes() == data:
        return resolved_target
    _atomic_write_bytes(resolved_target, data)
    return resolved_target


def _unlink_corrupt_cover_if_unchanged(
    path: Path,
    *,
    expected_data: bytes,
    expected_stat: os.stat_result,
) -> None:
    try:
        current_stat = path.stat()
        current_data = path.read_bytes()
    except FileNotFoundError:
        return
    identity = (
        current_stat.st_dev,
        current_stat.st_ino,
        current_stat.st_size,
        current_stat.st_mtime_ns,
    )
    expected_identity = (
        expected_stat.st_dev,
        expected_stat.st_ino,
        expected_stat.st_size,
        expected_stat.st_mtime_ns,
    )
    if identity == expected_identity and current_data == expected_data:
        path.unlink()


def _validated_cover_payload(data: bytes) -> tuple[str, bytes]:
    with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
        image_format = safe_text(image.format).upper()
        if image_format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError(f"unsupported product cover image format: {image_format or 'unknown'}")
        image.load()
        if image_format == "WEBP":
            output = BytesIO()
            decoded = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            decoded.save(output, format="PNG")
            return ".png", output.getvalue()
        return (".jpg" if image_format == "JPEG" else ".png"), data


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _cover_cache_path(*, category: str, uid: str, url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    url_hash = text_hash(url)[:12]
    return (
        PRODUCT_COVER_CACHE_ROOT
        / _safe_path_component(category or "uncategorized")
        / f"{_safe_path_component(uid or 'product')}-{url_hash}{suffix}"
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
        "templateVersion": safe_text(product_card.get("templateVersion")),
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
