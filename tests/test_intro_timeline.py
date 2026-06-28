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

    assert aligned["timing_source"]["type"] == "asr_scene_alignment"
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
