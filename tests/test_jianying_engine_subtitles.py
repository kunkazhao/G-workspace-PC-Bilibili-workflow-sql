from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


ENGINE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "jianying_engine" / "generate_jianying_draft.py"
ENGINE_DIR = ENGINE_SCRIPT.parent


def load_engine_module():
    module_name = "generate_jianying_draft_for_test"
    fake_qwen = types.ModuleType("qwen_asr")
    fake_qwen.Qwen3ForcedAligner = object
    previous_torch = sys.modules.get("torch")
    previous_qwen = sys.modules.get("qwen_asr")
    sys.modules["torch"] = types.ModuleType("torch")
    sys.modules["qwen_asr"] = fake_qwen
    sys.path.insert(0, str(ENGINE_DIR))
    try:
        spec = importlib.util.spec_from_file_location(module_name, ENGINE_SCRIPT)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(ENGINE_DIR))
        sys.modules.pop(module_name, None)
        if previous_torch is None:
            sys.modules.pop("torch", None)
        else:
            sys.modules["torch"] = previous_torch
        if previous_qwen is None:
            sys.modules.pop("qwen_asr", None)
        else:
            sys.modules["qwen_asr"] = previous_qwen


def test_alignment_asr_uses_shared_asr_service_and_can_disable_vad(tmp_path: Path, monkeypatch):
    module = load_engine_module()
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wave")
    captured: dict[str, object] = {}

    def fake_transcribe_segments(audio, *, model_name, language, beam_size, vad_filter):
        captured.update(
            {
                "audio_path": audio,
                "model_name": model_name,
                "language": language,
                "beam_size": beam_size,
                "vad_filter": vad_filter,
            }
        )
        return [{"start": 0.0, "end": 1.0, "text": "测试"}]

    monkeypatch.setattr(module.asr_service, "transcribe_segments", fake_transcribe_segments)

    segments = module.run_alignment_asr(audio_path, "base", "zh", vad_filter=False)

    assert segments == [{"start": 0.0, "end": 1.0, "text": "测试"}]
    assert captured == {
        "audio_path": audio_path,
        "model_name": "base",
        "language": "zh",
        "beam_size": 5,
        "vad_filter": False,
    }


def test_engine_subtitle_chunks_use_shared_bworkflow_rules():
    module = load_engine_module()

    chunks = module.split_transcript_clauses(
        "人声也不容易被糊住。颜值简约高级，可以连接App联动。降噪、音质、LDAC高清编码，蓝牙6.0和100元价位都要保留。"
    )

    assert chunks == [
        "人声也不容易被糊住",
        "颜值简约高级",
        "可以连接App联动",
        "降噪、音质、LDAC高清编码",
        "蓝牙6.0和100元价位都要保留",
    ]


def test_build_subtitle_segments_preserves_shared_rule_text(tmp_path: Path, monkeypatch):
    module = load_engine_module()
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wave")

    def fake_asr(*_: object, **__: object) -> list[dict[str, object]]:
        return [
            {"start": 0.0, "end": 1.0, "text": "降噪音质LDAC高清编码"},
            {"start": 1.0, "end": 2.0, "text": "蓝牙60也稳"},
        ]

    monkeypatch.setattr(module, "run_alignment_asr", fake_asr)

    segments = module.build_subtitle_segments(
        audio_path,
        "降噪、音质、LDAC高清编码，蓝牙6.0也稳。",
        0.0,
        "base",
        "zh",
    )

    assert [segment.text for segment in segments] == [
        "降噪、音质、LDAC高清编码",
        "蓝牙6.0也稳",
    ]


def test_build_subtitle_segments_keeps_chunks_when_asr_returns_one_large_segment(tmp_path: Path, monkeypatch):
    module = load_engine_module()
    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wave")

    def fake_asr(*_: object, **__: object) -> list[dict[str, object]]:
        return [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "漫步者FitBuds大厂出的稳妥款图个牌子靠谱又想要多模式降噪的人很合适",
            }
        ]

    monkeypatch.setattr(module, "run_alignment_asr", fake_asr)

    transcript = "漫步者FitBuds大厂出的稳妥款图个牌子靠谱、又想要多模式降噪的人很合适"
    expected_chunks = module.split_transcript_clauses(transcript)

    segments = module.build_subtitle_segments(
        audio_path,
        transcript,
        0.0,
        "base",
        "zh",
    )

    texts = [segment.text for segment in segments]
    assert texts == expected_chunks
    assert len(texts) > 1
    assert all(len(text) <= 24 for text in texts)
    assert all(
        segments[index].start_sec + segments[index].duration_sec <= segments[index + 1].start_sec - 0.015
        for index in range(len(segments) - 1)
    )
