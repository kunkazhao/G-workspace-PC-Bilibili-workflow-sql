from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .intro_plan_writer import _normalize_blank_lines, _project_markdown_path
from .settings import INTERNAL_WORKSPACE_ROOT
from .subtitle_rules import normalize_subtitle_alignment_text
from .utils import safe_text, text_hash


PRICE_TRANSITION_PLAN_VERSION = "1.0.0"
PRICE_TRANSITION_PLAN_FILENAME = "source-price-transition-plan-set.json"


@dataclass(frozen=True)
class PriceTransitionPlanWriteResult:
    plan_path: Path
    markdown_path: Path
    transition_count: int
    synced: bool
    sync_result: dict[str, Any] | None = None


def price_transition_authoring_workspace(project_id: int) -> Path:
    return INTERNAL_WORKSPACE_ROOT / f"project-{int(project_id)}" / "price-transitions"


def price_transition_plan_path(project_id: int) -> Path:
    return price_transition_authoring_workspace(project_id) / PRICE_TRANSITION_PLAN_FILENAME


def validate_price_transition_plan_set(payload: dict[str, Any]) -> dict[str, Any]:
    transitions = payload.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise ValueError("价格过渡计划必须包含非空 transitions 数组")

    normalized: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(transitions, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"价格过渡计划第 {index} 项必须是对象")
        price_range_label = safe_text(raw.get("price_range_label") or raw.get("priceRangeLabel"))
        block_label = safe_text(raw.get("block_label") or raw.get("blockLabel")) or "正文"
        transition_text = safe_text(raw.get("transition_text") or raw.get("transitionText")).strip()
        audience = safe_text(raw.get("audience")).strip()
        if not price_range_label:
            raise ValueError(f"价格过渡计划第 {index} 项缺少 price_range_label")
        if not transition_text:
            raise ValueError(f"价格过渡计划 {price_range_label}/{block_label} 缺少 transition_text")
        key = (price_range_label, block_label)
        if key in seen_keys:
            raise ValueError(f"价格过渡计划存在重复版本：{price_range_label}/{block_label}")
        seen_keys.add(key)

        items = _validate_items(
            raw.get("items"),
            transition_text=transition_text,
            context=f"{price_range_label}/{block_label}",
        )
        if not audience:
            raise ValueError(f"价格过渡计划 {price_range_label}/{block_label} 缺少 audience")
        normalized_audience = normalize_subtitle_alignment_text(audience)
        normalized_text = normalize_subtitle_alignment_text(transition_text)
        if normalized_audience not in normalized_text:
            raise ValueError(
                f"价格过渡计划 {price_range_label}/{block_label} 的 audience 必须原样出现在 transition_text 中"
            )

        normalized.append(
            {
                "price_range_label": price_range_label,
                "block_label": block_label,
                "transition_text": transition_text,
                "text_hash": text_hash(transition_text),
                "audience": audience,
                "items": items,
            }
        )

    return {
        "schema_version": PRICE_TRANSITION_PLAN_VERSION,
        "transitions": normalized,
    }


def _validate_items(raw_items: Any, *, transition_text: str, context: str) -> list[dict[str, str]]:
    if not isinstance(raw_items, list) or not 2 <= len(raw_items) <= 3:
        raise ValueError(f"价格过渡计划 {context} 必须有 2 到 3 个画面项")
    normalized_text = normalize_subtitle_alignment_text(transition_text)
    cursor = 0
    labels: set[str] = set()
    result: list[dict[str, str]] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"价格过渡计划 {context} 第 {index} 个画面项必须是对象")
        label = safe_text(raw.get("label") or raw.get("text")).strip()
        trigger_text = safe_text(raw.get("trigger_text") or raw.get("triggerText")).strip()
        if not label or not trigger_text:
            raise ValueError(f"价格过渡计划 {context} 第 {index} 个画面项缺少 label 或 trigger_text")
        if len(label) > 12:
            raise ValueError(f"价格过渡计划 {context} 的画面标签不能超过 12 个字符：{label}")
        if label in labels:
            raise ValueError(f"价格过渡计划 {context} 的画面标签重复：{label}")
        labels.add(label)
        normalized_trigger = normalize_subtitle_alignment_text(trigger_text)
        position = normalized_text.find(normalized_trigger, cursor)
        if position < 0:
            raise ValueError(
                f"价格过渡计划 {context} 的触发词必须按顺序原样出现在 transition_text 中：{trigger_text}"
            )
        cursor = position + len(normalized_trigger)
        result.append({"label": label, "trigger_text": trigger_text})
    return result


def write_price_transition_plan_for_project(
    *,
    db: Database,
    project_id: int,
    plan_input_path: str | Path,
    markdown_path: str | Path | None = None,
    sync: bool = False,
) -> PriceTransitionPlanWriteResult:
    from .sync_service import SyncService

    project = db.fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    if not project:
        raise ValueError(f"项目不存在：{project_id}")
    source = Path(plan_input_path)
    if not source.is_file():
        raise FileNotFoundError(f"价格过渡计划文件不存在：{source}")
    raw = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("价格过渡计划必须是 JSON 对象")
    plan = validate_price_transition_plan_set(raw)

    target_plan = price_transition_plan_path(project_id)
    target_plan.parent.mkdir(parents=True, exist_ok=True)
    target_plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    target_md = Path(markdown_path) if markdown_path else _project_markdown_path(db, project_id)
    for transition in plan["transitions"]:
        upsert_price_transition_markdown_block(
            target_md,
            price_range_label=transition["price_range_label"],
            block_label=transition["block_label"],
            body=transition["transition_text"],
        )
    db.execute("UPDATE projects SET md_path=?, updated_at=datetime('now') WHERE id=?", (str(target_md), project_id))

    sync_result = SyncService(db).sync_markdown(project_id) if sync else None
    return PriceTransitionPlanWriteResult(
        plan_path=target_plan,
        markdown_path=target_md,
        transition_count=len(plan["transitions"]),
        synced=sync,
        sync_result=sync_result,
    )


def load_price_transition_plan_set(project_id: int) -> dict[str, Any] | None:
    path = price_transition_plan_path(project_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"价格过渡源计划无法读取：{path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"价格过渡源计划格式无效：{path}")
    return validate_price_transition_plan_set(raw)


def find_price_transition_plan_for_text(
    project_id: int,
    *,
    price_range_label: str,
    block_label: str,
    body: str,
) -> dict[str, Any] | None:
    plan = load_price_transition_plan_set(project_id)
    if plan is None:
        return None
    expected_hash = text_hash(body)
    return next(
        (
            item
            for item in plan["transitions"]
            if safe_text(item.get("price_range_label")) == safe_text(price_range_label)
            and safe_text(item.get("block_label")) == (safe_text(block_label) or "正文")
            and safe_text(item.get("text_hash")) == expected_hash
        ),
        None,
    )


def price_transition_card_from_plan(plan: dict[str, Any], *, duration: float) -> dict[str, Any]:
    text = safe_text(plan.get("transition_text"))
    normalized_text = normalize_subtitle_alignment_text(text)
    visual_duration = max(float(duration or 0), 1.0)
    latest_start = max(0.45, visual_duration - 0.6)
    previous_start = -0.5
    items: list[dict[str, Any]] = []
    cursor = 0
    for raw in plan.get("items") or []:
        trigger_text = safe_text(raw.get("trigger_text"))
        trigger = normalize_subtitle_alignment_text(trigger_text)
        position = normalized_text.find(trigger, cursor)
        if position < 0:
            raise ValueError(f"价格过渡触发词与正文不一致：{trigger_text}")
        cursor = position + len(trigger)
        raw_start = (position / max(len(normalized_text), 1)) * visual_duration
        start = max(0.45, min(raw_start, latest_start))
        if start <= previous_start:
            start = min(latest_start, previous_start + 0.55)
        previous_start = start
        items.append(
            {
                "label": safe_text(raw.get("label")),
                "triggerText": trigger_text,
                "timing": {
                    "start": round(start, 3),
                    "duration": round(max(0.8, visual_duration - start), 3),
                },
            }
        )
    return {
        "rangeLabel": safe_text(plan.get("price_range_label")),
        "headline": "重点参数",
        "keyPoints": [item["label"] for item in items],
        "items": items,
        "visualEvents": _visual_events(items),
        "audience": safe_text(plan.get("audience")),
        "planVersion": PRICE_TRANSITION_PLAN_VERSION,
    }


def _visual_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "target": f"price_param_{index + 1:02d}",
            "text": item["label"],
            "trigger_text": item["triggerText"],
            "timing": item["timing"],
        }
        for index, item in enumerate(items)
    ]


def upsert_price_transition_markdown_block(
    markdown_path: str | Path,
    *,
    price_range_label: str,
    block_label: str,
    body: str,
) -> None:
    path = Path(markdown_path)
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = original.splitlines() or ["## 价格过渡文案", ""]
    section_start = _find_heading(lines, "##", "价格过渡文案")
    if section_start < 0:
        if lines and lines[-1].strip():
            lines.append("")
        section_start = len(lines)
        lines.extend(["## 价格过渡文案", ""])
    section_end = _next_heading(lines, section_start + 1, "##")
    if section_end < 0:
        section_end = len(lines)

    price_start = _find_heading(lines, "###", price_range_label, section_start + 1, section_end)
    if price_start < 0:
        insertion = [f"### {price_range_label}", "", f"#### {block_label}", "", body.strip(), ""]
        lines[section_end:section_end] = insertion
    else:
        price_end = _next_heading(lines, price_start + 1, "###", stop_level="##")
        if price_end < 0:
            price_end = len(lines)
        block_start = _find_heading(lines, "####", block_label, price_start + 1, price_end)
        if block_start < 0:
            lines[price_end:price_end] = [f"#### {block_label}", "", body.strip(), ""]
        else:
            block_end = _next_heading(lines, block_start + 1, "####", stop_level="###")
            if block_end < 0:
                block_end = len(lines)
            lines[block_start + 1:block_end] = ["", body.strip(), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_normalize_blank_lines(lines)).rstrip() + "\n", encoding="utf-8")


def _find_heading(
    lines: list[str],
    level: str,
    title: str,
    start: int = 0,
    end: int | None = None,
) -> int:
    wanted = f"{level} {title}"
    for index in range(start, len(lines) if end is None else end):
        if lines[index].strip() == wanted:
            return index
    return -1


def _next_heading(
    lines: list[str],
    start: int,
    level: str,
    *,
    stop_level: str | None = None,
) -> int:
    exact = re.compile(rf"^{re.escape(level)}\s+")
    stop = re.compile(rf"^{re.escape(stop_level)}\s+") if stop_level else None
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if exact.match(stripped) or (stop and stop.match(stripped)):
            return index
    return -1
