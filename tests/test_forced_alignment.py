from __future__ import annotations

from pathlib import Path

import pytest

from bworkflow_sql import forced_alignment


def test_items_map_exact_mixed_language_transcript_without_overlap() -> None:
    chunks = ["舒客H5", "经常出差住酒店"]
    items = [
        {"text": "舒", "start": 0.10, "end": 0.25},
        {"text": "客", "start": 0.25, "end": 0.40},
        {"text": "H5", "start": 0.40, "end": 0.86},
        {"text": "经", "start": 0.92, "end": 1.06},
        {"text": "常", "start": 1.06, "end": 1.20},
        {"text": "出", "start": 1.20, "end": 1.34},
        {"text": "差", "start": 1.34, "end": 1.48},
        {"text": "住", "start": 1.48, "end": 1.62},
        {"text": "酒", "start": 1.62, "end": 1.76},
        {"text": "店", "start": 1.76, "end": 1.90},
    ]

    result = forced_alignment.items_to_subtitle_segments(
        chunks,
        items,
        offset_sec=2.0,
        audio_duration_sec=2.2,
    )

    assert result == [
        (2.10, 2.86, "舒客H5"),
        (2.92, 3.90, "经常出差住酒店"),
    ]
    assert result[0][1] <= result[1][0]


def test_items_require_exact_transcript_coverage() -> None:
    with pytest.raises(ValueError, match="精确原文"):
        forced_alignment.items_to_subtitle_segments(
            ["usmileC30", "属于立式一体设计"],
            [
                {"text": "属于", "start": 1.0, "end": 1.4},
                {"text": "立式一体设计", "start": 1.4, "end": 2.4},
            ],
            offset_sec=0.0,
            audio_duration_sec=2.5,
        )


def test_items_keep_trailing_transcript_anchored_to_audio() -> None:
    result = forced_alignment.items_to_subtitle_segments(
        ["最后一句字幕"],
        [
            {"text": "最后一句", "start": 0.3, "end": 1.3},
            {"text": "字幕", "start": 1.3, "end": 2.1},
        ],
        offset_sec=0.0,
        audio_duration_sec=2.3,
    )

    assert result == [(0.3, 2.1, "最后一句字幕")]


def test_items_keep_display_only_plus_without_voice_anchor() -> None:
    result = forced_alignment.items_to_subtitle_segments(
        ["漫步者 R1700BT+"],
        [{"text": "漫步者R1700BT", "start": 0.0, "end": 1.2}],
        offset_sec=0.0,
        audio_duration_sec=1.3,
    )

    assert result == [(0.0, 1.2, "漫步者 R1700BT+")]


def test_point_anchor_inside_clause_does_not_invent_timing() -> None:
    result = forced_alignment.items_to_subtitle_segments(
        ["日常一轮清洁"],
        [
            {"text": "日常", "start": 1.0, "end": 1.4},
            {"text": "一", "start": 1.4, "end": 1.4},
            {"text": "轮清洁", "start": 1.4, "end": 2.1},
        ],
        offset_sec=0.0,
        audio_duration_sec=2.2,
    )

    assert result == [(1.0, 2.1, "日常一轮清洁")]


def test_grouped_alignment_caches_final_forced_results(tmp_path: Path, monkeypatch) -> None:
    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"audio")
    jobs = [{"audio_path": str(audio_path), "text": "舒客H5，经常出差", "label": "舒客 H5"}]
    calls: list[list[dict[str, object]]] = []

    def fake_worker(worker_jobs, **_kwargs):
        calls.append(worker_jobs)
        return [
            {
                "audio_duration_sec": 2.0,
                "items": [
                    {"text": "舒客H5", "start": 0.0, "end": 0.7},
                    {"text": "经常出差", "start": 0.8, "end": 1.8},
                ],
            }
        ]

    monkeypatch.setattr(forced_alignment, "FORCED_ALIGNMENT_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(forced_alignment, "run_forced_alignment_worker", fake_worker)

    first = forced_alignment.align_subtitle_jobs_with_forced_alignment_grouped(jobs)
    second = forced_alignment.align_subtitle_jobs_with_forced_alignment_grouped(jobs)

    assert first == second
    assert len(calls) == 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1


def test_grouped_alignment_reports_every_failed_segment(tmp_path: Path, monkeypatch) -> None:
    jobs = []
    for index in range(2):
        audio_path = tmp_path / f"voice-{index}.mp3"
        audio_path.write_bytes(f"audio-{index}".encode())
        jobs.append({"audio_path": str(audio_path), "text": "精确文案", "label": f"段落{index + 1}"})

    monkeypatch.setattr(forced_alignment, "FORCED_ALIGNMENT_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(
        forced_alignment,
        "run_forced_alignment_worker",
        lambda worker_jobs, **_kwargs: [
            {"audio_duration_sec": 1.0, "items": [{"text": "错误文案", "start": 0.0, "end": 0.8}]}
            for _job in worker_jobs
        ],
    )

    with pytest.raises(ValueError) as exc_info:
        forced_alignment.align_subtitle_jobs_with_forced_alignment_grouped(jobs)

    message = str(exc_info.value)
    assert "2 段" in message
    assert "段落1" in message
    assert "段落2" in message
