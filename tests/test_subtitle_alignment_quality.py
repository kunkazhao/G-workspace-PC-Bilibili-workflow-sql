from __future__ import annotations

from pathlib import Path

import pytest

from bworkflow_sql.subtitle_helpers import align_subtitle_text_with_units


def test_sequence_alignment_ignores_asr_insertion_before_script(tmp_path: Path):
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"audio")
    units = [
        {"start": 0.0, "end": 0.2, "text": "嗯"},
        {"start": 0.2, "end": 0.8, "text": "第一句"},
        {"start": 0.9, "end": 1.5, "text": "第二句"},
    ]

    aligned = align_subtitle_text_with_units(
        audio_path,
        ["第一句", "第二句"],
        units,
        0.0,
    )

    assert aligned == [(0.2, 0.8, "第一句"), (0.9, 1.5, "第二句")]


def test_sequence_alignment_blocks_low_quality_transcript(tmp_path: Path):
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"audio")
    units = [{"start": 0.0, "end": 1.0, "text": "完全不相关的内容"}]

    with pytest.raises(ValueError, match="ASR 对齐质量不达标"):
        align_subtitle_text_with_units(
            audio_path,
            ["这是需要精确对齐的原始口播文案"],
            units,
            0.0,
        )
