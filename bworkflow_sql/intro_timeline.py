from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .subtitle_helpers import (
    DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    DEFAULT_SUBTITLE_ASR_LANGUAGE,
    DEFAULT_SUBTITLE_ASR_MODEL,
    align_subtitle_text_with_units,
    normalize_subtitle_alignment_text,
    run_subtitle_alignment_asr,
)
from .utils import safe_text


def align_intro_plan_scenes_with_asr(
    intro_plan: dict[str, Any],
    audio_path: str | Path,
    *,
    offset_sec: float = 0.0,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
) -> dict[str, Any]:
    scenes = intro_plan_scenes(intro_plan)
    scene_texts = [scene["text"] for scene in scenes]
    validate_intro_scene_texts(intro_plan, scene_texts)

    units = run_subtitle_alignment_asr(
        audio_path,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
    )
    aligned = align_subtitle_text_with_units(audio_path, scene_texts, units, offset_sec)

    result = dict(intro_plan)
    result["scenes"] = list(intro_plan.get("scenes") or [])
    for scene, (start, end, _text) in zip(result["scenes"], aligned):
        scene["timing"] = {
            "start": round(float(start), 3),
            "duration": round(max(0.1, float(end) - float(start)), 3),
        }
    result["timing_source"] = {
        "type": "asr_scene_alignment",
        "model": model_name,
        "language": language,
        "beam_size": beam_size,
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
