from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .forced_alignment import (
    DEFAULT_FORCED_ALIGNMENT_LANGUAGE,
    DEFAULT_FORCED_ALIGNMENT_MODEL,
    forced_alignment_results,
    items_to_subtitle_segments,
)
from .subtitle_helpers import (
    DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    normalize_subtitle_alignment_text,
)
from .utils import safe_text


def align_intro_plan_scenes_with_asr(
    intro_plan: dict[str, Any],
    audio_path: str | Path,
    *,
    offset_sec: float = 0.0,
    model_name: str = DEFAULT_FORCED_ALIGNMENT_MODEL,
    language: str = DEFAULT_FORCED_ALIGNMENT_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
) -> dict[str, Any]:
    scenes = intro_plan_scenes(intro_plan)
    scene_texts = [scene["text"] for scene in scenes]
    validate_intro_scene_texts(intro_plan, scene_texts)

    forced_result = forced_alignment_results(
        [{
            "label": "引言场景与视觉事件",
            "audio_path": str(audio_path),
            "text": safe_text(intro_plan.get("full_script")) or "".join(scene_texts),
        }],
        model_name=model_name,
        language=language,
        batch_size=1,
    )[0]
    units = forced_result.get("items") if isinstance(forced_result, dict) else None
    if not isinstance(units, list):
        raise ValueError("引言强制对齐结果缺少文字锚点。")
    aligned = items_to_subtitle_segments(
        scene_texts,
        units,
        offset_sec=offset_sec,
        audio_duration_sec=float(forced_result.get("audio_duration_sec") or 0.0),
    )

    alignment_run_id = uuid.uuid4().hex
    result = dict(intro_plan)
    result["scenes"] = list(intro_plan.get("scenes") or [])
    for scene, (start, end, _text) in zip(result["scenes"], aligned):
        scene["timing"] = {
            "start": round(float(start), 3),
            "duration": round(max(0.1, float(end) - float(start)), 3),
        }

    result["visual_events"] = align_visual_event_specs_with_units(
        result,
        units,
        offset_sec=offset_sec,
        alignment_run_id=alignment_run_id,
    )
    result["timing_source"] = {
        "type": "forced_transcript_scene_and_visual_event_alignment",
        "model": model_name,
        "language": language,
        "alignment_run_id": alignment_run_id,
    }
    return result


def align_intro_plan_file_with_asr(
    intro_plan_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    plan_path = Path(intro_plan_path)
    intro_plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    aligned = align_intro_plan_scenes_with_asr(intro_plan, audio_path, **kwargs)

    target = Path(output_path) if output_path else plan_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(aligned, ensure_ascii=False, indent=2), encoding="utf-8")
    return aligned


def intro_plan_scenes(intro_plan: dict[str, Any]) -> list[dict[str, str]]:
    raw_scenes = intro_plan.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("intro_plan 缺少 scenes")

    scenes: list[dict[str, str]] = []
    for index, scene in enumerate(raw_scenes, start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"intro_plan scenes[{index}] 不是对象")
        scene_type = safe_text(scene.get("type"))
        text = safe_text(scene.get("text"))
        if not scene_type:
            raise ValueError(f"intro_plan scenes[{index}] 缺少 type")
        if not text:
            raise ValueError(f"intro_plan scenes[{index}] 缺少 text")
        scenes.append({"type": scene_type, "text": text})
    return scenes


def validate_intro_scene_texts(intro_plan: dict[str, Any], scene_texts: list[str]) -> None:
    full_script = safe_text(intro_plan.get("full_script"))
    if not full_script:
        return
    expected = normalize_subtitle_alignment_text(full_script)
    actual = normalize_subtitle_alignment_text("".join(scene_texts))
    if expected != actual:
        raise ValueError("intro_plan scenes[].text 拼接后与 full_script 不一致，不能做 ASR 场景对齐")


def align_visual_event_specs_with_units(
    intro_plan: dict[str, Any],
    units: list[dict[str, Any]],
    *,
    offset_sec: float = 0.0,
    alignment_run_id: str = "",
) -> list[dict[str, Any]]:
    specs = [
        spec
        for spec in intro_plan.get("visual_event_specs") or []
        if isinstance(spec, dict)
    ]
    if not specs:
        return []

    expanded_units = _expand_units_for_visual_events(units)
    scenes = intro_plan_scenes(intro_plan)
    scene_offsets = _normalized_scene_offsets(scenes)
    normalized_full = normalize_subtitle_alignment_text(
        safe_text(intro_plan.get("full_script")) or "".join(scene["text"] for scene in scenes)
    )

    events: list[dict[str, Any]] = []
    for spec in specs:
        event = dict(spec)
        scene_type = safe_text(spec.get("scene_type"))
        trigger_text = safe_text(spec.get("trigger_text"))
        timing = _align_visual_event_timing(
            spec=spec,
            scene_offsets=scene_offsets,
            normalized_full=normalized_full,
            expanded_units=expanded_units,
            offset_sec=offset_sec,
            intro_plan=intro_plan,
        )
        if timing is None:
            event_id = safe_text(spec.get("id")) or safe_text(spec.get("target")) or "未命名视觉事件"
            raise ValueError(
                f"引言视觉事件 {event_id} 的触发词无法在精确原文中定位：{trigger_text}"
            )
        event["timing"] = timing
        event["alignment"] = {
            "source": timing.get("source") or "unknown",
            "trigger_text": trigger_text,
            "scene_type": scene_type,
        }
        if alignment_run_id:
            event["alignment"]["alignment_run_id"] = alignment_run_id
        events.append(event)
    return events


def _align_visual_event_timing(
    *,
    spec: dict[str, Any],
    scene_offsets: dict[str, tuple[int, int]],
    normalized_full: str,
    expanded_units: list[dict[str, Any]],
    offset_sec: float,
    intro_plan: dict[str, Any],
) -> dict[str, Any] | None:
    trigger = normalize_subtitle_alignment_text(safe_text(spec.get("trigger_text")))
    scene_type = safe_text(spec.get("scene_type"))
    if not trigger or not expanded_units or not normalized_full:
        return None

    scene_range = scene_offsets.get(scene_type)
    search_start = scene_range[0] if scene_range else 0
    search_end = scene_range[1] if scene_range else len(normalized_full)
    pos = normalized_full.find(trigger, search_start, search_end)
    if pos < 0:
        pos = normalized_full.find(trigger)
    if pos < 0:
        return None

    unit_start = _char_offset_to_unit_index(pos, len(normalized_full), len(expanded_units))
    unit_end = _char_offset_to_unit_index(
        min(len(normalized_full), pos + max(len(trigger), 1)) - 1,
        len(normalized_full),
        len(expanded_units),
    )
    start_unit = expanded_units[max(0, min(unit_start, len(expanded_units) - 1))]
    end_unit = expanded_units[max(0, min(unit_end, len(expanded_units) - 1))]
    start = max(0.0, offset_sec + float(start_unit.get("start") or 0.0))
    end = max(start + 0.1, offset_sec + float(end_unit.get("end") or start + 0.1))

    scene_timing = _scene_timing(intro_plan, scene_type)
    if scene_timing:
        scene_start, scene_end = scene_timing
        start = min(max(start, scene_start), max(scene_start, scene_end - 0.1))
        end = min(max(end, start + 0.1), scene_end)

    return {
        "start": round(start, 3),
        "duration": round(max(0.25, min(0.8, end - start)), 3),
        "source": "forced_alignment_trigger_text",
    }


def _expanded_unit(start: float, end: float, text: str) -> list[dict[str, Any]]:
    clean = normalize_subtitle_alignment_text(text)
    if not clean:
        return []
    start = max(0.0, float(start or 0.0))
    end = max(start + 0.001, float(end or start))
    step = (end - start) / len(clean)
    return [
        {"start": start + step * index, "end": start + step * (index + 1), "text": char}
        for index, char in enumerate(clean)
    ]


def _expand_units_for_visual_events(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        expanded.extend(
            _expanded_unit(
                float(unit.get("start") or 0.0),
                float(unit.get("end") or 0.0),
                safe_text(unit.get("text")),
            )
        )
    return expanded


def _normalized_scene_offsets(scenes: list[dict[str, str]]) -> dict[str, tuple[int, int]]:
    offsets: dict[str, tuple[int, int]] = {}
    cursor = 0
    for scene in scenes:
        normalized = normalize_subtitle_alignment_text(scene["text"])
        start = cursor
        cursor += len(normalized)
        offsets[scene["type"]] = (start, cursor)
    return offsets


def _char_offset_to_unit_index(
    char_offset: int,
    normalized_length: int,
    unit_count: int,
) -> int:
    if unit_count != normalized_length:
        raise ValueError(
            "引言视觉事件的强制对齐锚点没有完整覆盖精确原文："
            f"原文 {normalized_length} 字，对齐 {unit_count} 字。"
        )
    return max(0, min(char_offset, unit_count - 1))


def _scene_timing(intro_plan: dict[str, Any], scene_type: str) -> tuple[float, float] | None:
    for scene in intro_plan.get("scenes") or []:
        if not isinstance(scene, dict) or safe_text(scene.get("type")) != scene_type:
            continue
        timing = scene.get("timing")
        if not isinstance(timing, dict):
            return None
        try:
            start = float(timing.get("start"))
            duration = float(timing.get("duration"))
        except (TypeError, ValueError):
            return None
        if duration <= 0:
            return None
        return start, start + duration
    return None
