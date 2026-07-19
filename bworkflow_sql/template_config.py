from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# 每个模板的视频展示区域坐标（相对于 1920*1080 画布）
# 数据来源：G:\workspace\PC-Bilibili-workflow\data\display_video_templates.json
#
# ── 剪映 UI 参数 ↔ 本文件坐标的转换公式 ──
# 剪映X = (center_x - 960) × 2      center_x = 960 + 剪映X / 2
# 剪映Y = (540 - center_y) × 2      center_y = 540 - 剪映Y / 2
# 剪映缩放% = display_scale × 100
# 其中 center_x = x + width/2, center_y = y + height/2
# 注意：乘/除 2，不是乘/除 960/540，之前在这里踩过坑。
TEMPLATE_COORDS: dict[str, dict[str, Any]] = {
    "小博-模板1": {"x": 850, "y": 95, "width": 980, "height": 620},
    "小博-模板2": {"x": 1015, "y": 154, "width": 680, "height": 520, "display_scale": 0.52},
    "小博-模板3": {"x": 1015, "y": 154, "width": 680, "height": 520, "display_scale": 0.52},
    "小燃-模板1": {
        "x": -830,
        "y": -77,
        "width": 970,
        "height": 590,
        "coordinate_mode": "clip_transform_pixels",
        "scale_x": 970 / 1936,
        "scale_y": 590 / 1080,
    },
    "小燃-模板2": {"x": 47, "y": 317, "width": 1003, "height": 588, "display_scale": 0.55},
    "小歪-模板1": {"x": -855, "y": -22, "width": 960, "height": 540, "coordinate_mode": "clip_transform_pixels"},
    "小歪-模板2": {"x": -29, "y": 202, "width": 1132, "height": 676, "display_scale": 0.53},
    "知了-模板1": {"x": 67, "y": 185, "width": 990, "height": 576},
    "荣荣-模板1": {"x": 115, "y": 200, "width": 941, "height": 554},
    "荣荣-模板2": {"x": 44, "y": 172, "width": 851, "height": 436, "display_scale": 0.42},
}

PRODUCT_CARD_TEMPLATE_IDS: dict[str, str] = {
    "xiaobo1": "小博-模板1",
    "xiaobo2": "小博-模板2",
    "xiaobo3": "小博-模板3",
    "xiaoran1": "小燃-模板1",
    "xiaoran2": "小燃-模板2",
    "xiaowai1": "小歪-模板1",
    "xiaowai2": "小歪-模板2",
    "zhiliao1": "知了-模板1",
    "rongrong1": "荣荣-模板1",
    "rongrong2": "荣荣-模板2",
}

# 每个用户对应的可用模板列表
USER_TEMPLATES: dict[str, list[str]] = {
    "小博": ["小博-模板1", "小博-模板2", "小博-模板3"],
    "小燃": ["小燃-模板1", "小燃-模板2"],
    "小歪": ["小歪-模板1", "小歪-模板2"],
    "知了": ["知了-模板1"],
    "荣荣": ["荣荣-模板1", "荣荣-模板2"],
}

REMOTION_TEMPLATE_METADATA_PATH = Path(
    os.environ.get(
        "CUTME_REMOTION_TEMPLATE_METADATA",
        str(
            Path(__file__).resolve().parents[2]
            / "赵二-工具-CutMe"
            / "remotion-renderer"
            / "product-card-templates.json"
        ),
    )
)


@lru_cache(maxsize=1)
def _remotion_template_metadata() -> dict[str, dict[str, Any]]:
    if not REMOTION_TEMPLATE_METADATA_PATH.exists():
        return {}
    try:
        payload = json.loads(REMOTION_TEMPLATE_METADATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    templates = payload.get("templates")
    if not isinstance(templates, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in templates:
        if not isinstance(item, dict):
            continue
        template_id = str(item.get("templateId") or "").strip()
        if template_id:
            result[template_id] = dict(item)
    return result


def get_remotion_template_metadata(template_id: str) -> dict[str, Any]:
    """读取 CutMe Remotion-first 商品图模板元数据。"""
    metadata = _remotion_template_metadata().get(template_id.strip())
    if metadata is None:
        raise ValueError(f"未知 Remotion 商品图模板：{template_id}")
    return dict(metadata)


def product_card_text_capacity_certification_issues(
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate the hash-bound text-capacity approval for a product-card template."""
    template_id = str(metadata.get("templateId") or "").strip()
    template_version = str(metadata.get("templateVersion") or "").strip()
    certification = metadata.get("textCapacityCertification")
    if not isinstance(certification, dict) or certification.get("status") != "approved":
        return [
            {
                "level": "error",
                "code": "text_capacity_uncertified",
                "template_id": template_id,
                "message": (
                    "Product-card template has no approved text-capacity certification; "
                    "formal image/video rendering is blocked."
                ),
            }
        ]

    issues: list[dict[str, Any]] = []
    certified_version = str(certification.get("templateVersion") or "").strip()
    if not template_version or certified_version != template_version:
        issues.append(
            {
                "level": "error",
                "code": "text_capacity_template_version_mismatch",
                "template_id": template_id,
                "expected": template_version,
                "certified": certified_version,
                "message": "Text-capacity certification does not match the registered template version.",
            }
        )

    renderer_root = REMOTION_TEMPLATE_METADATA_PATH.parent.resolve()
    component_source = str(certification.get("componentSource") or "").strip()
    source_path = (renderer_root / component_source).resolve() if component_source else None
    if source_path is None or renderer_root not in source_path.parents or not source_path.is_file():
        issues.append(
            {
                "level": "error",
                "code": "text_capacity_component_source_missing",
                "template_id": template_id,
                "component_source": component_source,
                "message": "Certified product-card component source is missing or outside CutMe.",
            }
        )
    else:
        actual_source_hash = _sha256_prefixed(source_path)
        certified_source_hash = str(certification.get("sourceSha256") or "").strip()
        if certified_source_hash != actual_source_hash:
            issues.append(
                {
                    "level": "error",
                    "code": "text_capacity_source_hash_mismatch",
                    "template_id": template_id,
                    "expected": certified_source_hash,
                    "actual": actual_source_hash,
                    "message": "Product-card component changed after text-capacity approval.",
                }
            )

    supporting_sources = certification.get("supportingSources", [])
    if not isinstance(supporting_sources, list):
        issues.append(
            {
                "level": "error",
                "code": "text_capacity_supporting_source_invalid",
                "template_id": template_id,
                "message": "Certified supportingSources must be a list of path/hash records.",
            }
        )
    else:
        for index, supporting_source in enumerate(supporting_sources):
            record = supporting_source if isinstance(supporting_source, dict) else {}
            relative_path = str(record.get("path") or "").strip()
            resolved_path = (renderer_root / relative_path).resolve() if relative_path else None
            if (
                resolved_path is None
                or renderer_root not in resolved_path.parents
                or not resolved_path.is_file()
            ):
                issues.append(
                    {
                        "level": "error",
                        "code": "text_capacity_supporting_source_missing",
                        "template_id": template_id,
                        "source_index": index,
                        "path": relative_path,
                        "message": "A certified text-fit supporting source is missing or outside CutMe.",
                    }
                )
                continue
            actual_hash = _sha256_prefixed(resolved_path)
            certified_hash = str(record.get("sha256") or "").strip()
            if certified_hash != actual_hash:
                issues.append(
                    {
                        "level": "error",
                        "code": "text_capacity_supporting_source_hash_mismatch",
                        "template_id": template_id,
                        "source_index": index,
                        "path": relative_path,
                        "expected": certified_hash,
                        "actual": actual_hash,
                        "message": "A text-fit supporting source changed after text-capacity approval.",
                    }
                )

    baseline_path = renderer_root / "product-card-text-capacity-baseline.json"
    if not baseline_path.is_file():
        issues.append(
            {
                "level": "error",
                "code": "text_capacity_baseline_missing",
                "template_id": template_id,
                "message": "Product-card text-capacity baseline is missing.",
            }
        )
    else:
        actual_baseline_hash = _sha256_prefixed(baseline_path)
        certified_baseline_hash = str(certification.get("baselineSha256") or "").strip()
        if certified_baseline_hash != actual_baseline_hash:
            issues.append(
                {
                    "level": "error",
                    "code": "text_capacity_baseline_hash_mismatch",
                    "template_id": template_id,
                    "expected": certified_baseline_hash,
                    "actual": actual_baseline_hash,
                    "message": "Text-capacity baseline changed after template approval.",
                }
            )
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            baseline = {}
        baseline_schema = baseline.get("schemaVersion") if isinstance(baseline, dict) else None
        if certification.get("baselineSchemaVersion") != baseline_schema:
            issues.append(
                {
                    "level": "error",
                    "code": "text_capacity_baseline_version_mismatch",
                    "template_id": template_id,
                    "expected": baseline_schema,
                    "certified": certification.get("baselineSchemaVersion"),
                    "message": "Text-capacity certification targets another baseline schema version.",
                }
            )
    return issues


def _sha256_prefixed(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _remotion_template_by_display_name(template_name: str) -> dict[str, Any] | None:
    normalized = template_name.strip()
    if not normalized:
        return None
    for metadata in _remotion_template_metadata().values():
        if str(metadata.get("displayName") or "").strip() == normalized:
            return dict(metadata)
    return None


def _project_remotion_cover_slot(metadata: dict[str, Any]) -> dict[str, Any]:
    slot = metadata.get("videoOverlaySlot") or metadata.get("coverMediaSlot")
    source = metadata.get("sourceCanvas")
    placement = metadata.get("cardPlacement")
    output = metadata.get("outputCanvas")
    if not all(isinstance(item, dict) for item in (slot, source, placement, output)):
        raise ValueError("Remotion 模板元数据缺少 coverMediaSlot/sourceCanvas/cardPlacement/outputCanvas")

    source_width = float(source.get("width") or slot.get("sourceWidth") or 0)
    source_height = float(source.get("height") or slot.get("sourceHeight") or 0)
    if source_width <= 0 or source_height <= 0:
        raise ValueError("Remotion 模板 sourceCanvas 不合法")

    scale_x = float(placement.get("width") or 0) / source_width
    scale_y = float(placement.get("height") or 0) / source_height
    projected = {
        "x": round(float(placement.get("x") or 0) + float(slot.get("x") or 0) * scale_x),
        "y": round(float(placement.get("y") or 0) + float(slot.get("y") or 0) * scale_y),
        "width": round(float(slot.get("width") or 0) * scale_x),
        "height": round(float(slot.get("height") or 0) * scale_y),
        "sourceWidth": int(output.get("width") or 1920),
        "sourceHeight": int(output.get("height") or 1080),
        "coordinate_mode": "canvas_rect",
        "templateId": str(metadata.get("templateId") or ""),
        "templateVersion": str(metadata.get("templateVersion") or ""),
    }
    display_scale = metadata.get("displayScale")
    if display_scale is not None:
        projected["display_scale"] = float(display_scale)
    return projected


def display_video_slot_for_product_card_template_id(template_id: str) -> dict[str, Any]:
    """根据 Remotion-first templateId 推导剪映商品视频展示槽位。"""
    metadata = get_remotion_template_metadata(template_id)
    return _project_remotion_cover_slot(metadata)


def get_template_slot(template_name: str) -> dict[str, Any]:
    """根据模板名称查询视频展示区域坐标。"""
    coords = TEMPLATE_COORDS.get(template_name)
    if coords is not None:
        return dict(coords)
    remotion_metadata = _remotion_template_by_display_name(template_name)
    if remotion_metadata is not None:
        return _project_remotion_cover_slot(remotion_metadata)
    raise ValueError(f"未知模板：{template_name}")


def display_template_for_product_card_template_id(template_id: str) -> str:
    """把商品图模板 ID 映射到剪映显示模板名。"""
    raw = template_id.strip()
    if raw in _remotion_template_metadata():
        return str(_remotion_template_metadata()[raw].get("displayName") or "")
    normalized = "".join(ch for ch in raw.casefold() if ch.isalnum())
    return PRODUCT_CARD_TEMPLATE_IDS.get(normalized, "")


def remotion_template_id_for_user(user_label: str) -> str:
    account = user_label.strip()
    if not account:
        return ""
    for metadata in _remotion_template_metadata().values():
        if str(metadata.get("account") or "").strip() != account:
            continue
        template_id = str(metadata.get("templateId") or "").strip()
        if template_id:
            return template_id
    return ""


def resolve_product_card_template(
    account_label: str,
    product_card_template_id: str = "",
    *,
    require_explicit: bool = False,
) -> dict[str, Any]:
    """Resolve the Remotion-first product-card template for one generation task.

    The explicit template id/display name wins. If omitted, the account's first
    Remotion template is the default. Account validation prevents a stale product
    record or CLI typo from silently selecting another user's visual contract.
    """
    account = account_label.strip()
    requested = product_card_template_id.strip()
    metadata: dict[str, Any] | None = None
    if requested:
        if requested in _remotion_template_metadata():
            metadata = dict(_remotion_template_metadata()[requested])
        else:
            metadata = _remotion_template_by_display_name(requested)
        if metadata is None:
            raise ValueError(f"unknown Remotion product-card template: {requested}")
    else:
        if require_explicit and remotion_template_id_for_user(account):
            options = ", ".join(_remotion_template_names_for_user(account))
            suffix = f" 可选模板：{options}" if options else ""
            raise ValueError(
                f"生成商品图前必须明确选择商品图模板，请传 --product-card-template-id。{suffix}"
            )
        default_template_id = remotion_template_id_for_user(account)
        if default_template_id:
            metadata = get_remotion_template_metadata(default_template_id)

    if metadata is None:
        return {}

    template_account = str(metadata.get("account") or "").strip()
    template_id = str(metadata.get("templateId") or "").strip()
    if account and template_account and template_account != account:
        raise ValueError(
            f"Remotion product-card template {template_id} does not belong to account {account}"
        )
    return dict(metadata)


def _remotion_template_names_for_user(user_label: str) -> list[str]:
    account = user_label.strip()
    names: list[str] = []
    if not account:
        return names
    for metadata in _remotion_template_metadata().values():
        if str(metadata.get("account") or "").strip() != account:
            continue
        display_name = str(metadata.get("displayName") or "").strip()
        if display_name and display_name not in names:
            names.append(display_name)
    return names


def available_templates(user_label: str) -> list[str]:
    """获取某个用户可用的模板列表。"""
    templates: list[str] = []
    for template_name in [
        *_remotion_template_names_for_user(user_label),
        *USER_TEMPLATES.get(user_label, []),
    ]:
        if template_name and template_name not in templates:
            templates.append(template_name)
    return templates


def image_set_for_template(template_name: str) -> str:
    """根据模板显示名推导素材目录关键字。"""
    if not template_name:
        return ""
    if "-" in template_name:
        return template_name.split("-", 1)[1]
    match = re.match(r"^(.+?)(模板\d+)$", template_name)
    if match:
        return match.group(2)
    return template_name


def display_template_from_image_path(image_path: str, *, account_label: str) -> str:
    account = account_label.strip()
    if not image_path or not account:
        return ""
    parts = [part for part in re.split(r"[\\/]+", image_path) if part]
    for index, part in enumerate(parts[:-1]):
        if part != account:
            continue
        template_dir = parts[index + 1]
        if not template_dir.startswith("模板"):
            continue
        candidate = f"{account}-{template_dir}"
        try:
            get_template_slot(candidate)
        except ValueError:
            continue
        return candidate
    return ""


def user_for_template(template_name: str) -> str:
    """根据模板名反查所属用户。"""
    for user, templates in USER_TEMPLATES.items():
        if template_name in templates:
            return user
    remotion_metadata = _remotion_template_by_display_name(template_name)
    if remotion_metadata is not None:
        return str(remotion_metadata.get("account") or "")
    return ""
