from __future__ import annotations

import math
import wave
from pathlib import Path

import pytest

import bworkflow_sql.intro_timeline as intro_timeline_module


def write_test_wav(path: Path, segments: list[tuple[float, float]], *, frame_rate: int = 1000) -> None:
    frames = bytearray()
    amplitude = 12000
    for duration_sec, volume in segments:
        frame_count = int(duration_sec * frame_rate)
        for index in range(frame_count):
            value = int(math.sin(index / 8) * amplitude * volume)
            frames.extend(value.to_bytes(2, byteorder="little", signed=True))

    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(frame_rate)
        writer.writeframes(bytes(frames))


def test_align_intro_plan_scenes_with_asr_writes_scene_timing(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "intro.wav"
    write_test_wav(audio_path, [(0.5, 0.6), (0.2, 0.0), (0.5, 0.6), (0.2, 0.0), (0.5, 0.6)])
    plan = {
        "full_script": "第一句。第二句。第三句。",
        "scenes": [
            {"type": "hook_open", "text": "第一句。"},
            {"type": "pain_points", "text": "第二句。"},
            {"type": "self_check", "text": "第三句。"},
        ],
    }

    def fake_asr(_audio_path, *, model_name, language, beam_size):
        assert model_name == intro_timeline_module.DEFAULT_SUBTITLE_ASR_MODEL
        assert language == intro_timeline_module.DEFAULT_SUBTITLE_ASR_LANGUAGE
        assert beam_size == intro_timeline_module.DEFAULT_SUBTITLE_ASR_BEAM_SIZE
        return [
            {"start": 0.0, "end": 0.15, "text": "第"},
            {"start": 0.15, "end": 0.3, "text": "一"},
            {"start": 0.3, "end": 0.5, "text": "句"},
            {"start": 0.7, "end": 0.85, "text": "第"},
            {"start": 0.85, "end": 1.0, "text": "二"},
            {"start": 1.0, "end": 1.2, "text": "句"},
            {"start": 1.4, "end": 1.55, "text": "第"},
            {"start": 1.55, "end": 1.7, "text": "三"},
            {"start": 1.7, "end": 1.9, "text": "句"},
        ]

    monkeypatch.setattr(intro_timeline_module, "run_subtitle_alignment_asr", fake_asr)

    aligned = intro_timeline_module.align_intro_plan_scenes_with_asr(plan, audio_path)

    assert aligned["timing_source"]["type"] == "asr_scene_and_visual_event_alignment"
    assert aligned["scenes"][0]["timing"] == {"start": 0.0, "duration": 0.5}
    assert aligned["scenes"][1]["timing"]["start"] == pytest.approx(0.7)
    assert aligned["scenes"][2]["timing"]["start"] == pytest.approx(1.4)


def test_align_intro_plan_scenes_rejects_changed_scene_text(tmp_path: Path):
    audio_path = tmp_path / "intro.wav"
    write_test_wav(audio_path, [(1.0, 0.6)])
    plan = {
        "full_script": "第一句。第二句。",
        "scenes": [
            {"type": "hook_open", "text": "第一句。"},
            {"type": "pain_points", "text": "被改过。"},
        ],
    }

    with pytest.raises(ValueError, match="full_script"):
        intro_timeline_module.align_intro_plan_scenes_with_asr(plan, audio_path)


def test_align_visual_event_specs_with_units_uses_trigger_text():
    plan = {
        "full_script": "Buy keyboard. Noise hurts. Hands tire.",
        "scenes": [
            {
                "type": "hook_open",
                "text": "Buy keyboard.",
                "timing": {"start": 0.0, "duration": 1.0},
            },
            {
                "type": "pain_points",
                "text": "Noise hurts. Hands tire.",
                "timing": {"start": 1.0, "duration": 3.0},
            },
        ],
        "visual_event_specs": [
            {
                "id": "pain_1",
                "scene_type": "pain_points",
                "target": "pain_points.cards[0]",
                "trigger_text": "Noise hurts",
                "order": 0,
                "animation": "card_reveal",
            },
            {
                "id": "pain_2",
                "scene_type": "pain_points",
                "target": "pain_points.cards[1]",
                "trigger_text": "Hands tire",
                "order": 1,
                "animation": "card_reveal",
            },
        ],
    }
    units = [
        {"start": 0.0, "end": 1.0, "text": "Buy keyboard."},
        {"start": 1.0, "end": 2.0, "text": "Noise hurts."},
        {"start": 2.3, "end": 3.3, "text": "Hands tire."},
    ]

    events = intro_timeline_module.align_visual_event_specs_with_units(plan, units)

    assert [event["target"] for event in events] == [
        "pain_points.cards[0]",
        "pain_points.cards[1]",
    ]
    assert events[0]["timing"]["source"] == "asr_trigger_text"
    assert events[0]["timing"]["start"] == pytest.approx(1.0, abs=0.15)
    assert events[1]["timing"]["start"] == pytest.approx(2.3, abs=0.15)
