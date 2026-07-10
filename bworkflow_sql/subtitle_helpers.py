from __future__ import annotations

import json
import os
import subprocess
import tempfile
import wave
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .asr import service as asr_service
from .asr.providers.faster_whisper import DEFAULT_ASR_PYTHON, DEFAULT_ASR_WORKER
from .utils import safe_text
from .subtitle_rules import (
    SUBTITLE_ALIGN_DROP_RE,
    SUBTITLE_BREAK_RE,
    SUBTITLE_DROP_PUNCT_RE,
    normalize_subtitle_alignment_text,
    split_subtitle_text,
)
from .tts_helpers import (
    DEFAULT_SILENCE_THRESHOLD_DB,
    DEFAULT_SILENCE_CHUNK_MS,
    seconds_to_frames,
    silence_ranges_for_audio,
)

DEFAULT_SUBTITLE_ASR_MODEL = "base"
DEFAULT_SUBTITLE_ASR_LANGUAGE = "zh"
DEFAULT_SUBTITLE_ASR_BEAM_SIZE = 2
DEFAULT_SUBTITLE_ASR_WORKERS = 3
DEFAULT_SUBTITLE_SPEECH_SNAP_WINDOW_SEC = 0.5
DEFAULT_SUBTITLE_OVERLAP_GAP_SEC = 0.02
DEFAULT_SUBTITLE_ASR_MIN_COVERAGE = 0.6
DEFAULT_SUBTITLE_ASR_PYTHON = DEFAULT_ASR_PYTHON
DEFAULT_SUBTITLE_ASR_WORKER = DEFAULT_ASR_WORKER


def subtitle_manifest_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_entries = payload.get("entries") or payload.get("items") or []
    elif isinstance(payload, list):
        raw_entries = payload
    else:
        raw_entries = []
    entries = [entry for entry in raw_entries if isinstance(entry, dict)]
    return sorted(entries, key=lambda entry: int(entry.get("order_index") or entry.get("section_order") or 0))


def subtitle_entry_label(entry: dict[str, Any]) -> str:
    parts = [
        f"#{entry.get('order_index') or entry.get('section_order')}" if entry.get("order_index") or entry.get("section_order") else "",
        safe_text(entry.get("section") or entry.get("type")),
        safe_text(entry.get("product_uid")),
        safe_text(entry.get("product_name") or entry.get("source_label")),
    ]
    return " ".join(part for part in parts if part) or "未命名字幕段"


def probe_media_duration_seconds(path: Path) -> float:
    if path.suffix.casefold() == ".wav":
        try:
            with wave.open(str(path), "rb") as reader:
                frame_rate = reader.getframerate()
                frame_count = reader.getnframes()
            if frame_rate > 0 and frame_count > 0:
                return frame_count / frame_rate
        except wave.Error:
            pass
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    if completed.returncode != 0:
        raise ValueError(f"无法读取媒体时长：{path}\n{completed.stderr.strip()}")
    payload = json.loads(completed.stdout or "{}")
    duration_text = safe_text(payload.get("format", {}).get("duration"))
    if not duration_text:
        raise ValueError(f"无法读取媒体时长：{path}")
    duration = float(duration_text)
    if duration <= 0:
        raise ValueError(f"媒体时长必须大于 0：{path}")
    return duration


def distribute_subtitle_text(text: str, start_sec: float, duration_sec: float) -> list[tuple[float, float, str]]:
    chunks = split_subtitle_text(text)
    if not chunks:
        return []
    total_weight = sum(max(len(chunk), 1) for chunk in chunks)
    cursor = start_sec
    segments: list[tuple[float, float, str]] = []
    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            end = start_sec + duration_sec
        else:
            end = cursor + duration_sec * (max(len(chunk), 1) / total_weight)
        if end <= cursor:
            end = cursor + 0.1
        segments.append((cursor, end, chunk))
        cursor = end
    return segments


def _expand_asr_unit(start: float, end: float, text: str) -> list[dict[str, Any]]:
    clean = normalize_subtitle_alignment_text(text)
    if not clean:
        return []
    start = max(0.0, float(start or 0.0))
    end = max(start + 0.001, float(end or start))
    step = (end - start) / len(clean)
    return [
        {
            "start": start + step * index,
            "end": start + step * (index + 1),
            "text": char,
        }
        for index, char in enumerate(clean)
    ]


def subtitle_asr_python_path() -> Path:
    return asr_service.get_provider("faster_whisper").python_path()


def run_subtitle_asr_worker(
    jobs: list[dict[str, Any]],
    *,
    model_name: str,
    language: str,
    beam_size: int,
    workers: int,
    provider_name: str | None = None,
) -> list[list[dict[str, Any]]]:
    return asr_service.transcribe_jobs(
        jobs,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        workers=workers,
        provider_name=provider_name,
    )


def run_subtitle_alignment_asr(
    audio_path: str | Path,
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    provider_name: str | None = None,
) -> list[dict[str, Any]]:
    path = Path(audio_path)
    if not path.exists():
        raise ValueError(f"音频文件不存在：{path}")
    return run_subtitle_asr_worker(
        [{"audio_path": str(path)}],
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        workers=1,
        provider_name=provider_name,
    )[0]


def subtitle_speech_ranges(audio_path: str | Path) -> list[tuple[float, float]]:
    path = Path(audio_path)
    if path.suffix.casefold() != ".wav":
        return []
    try:
        with wave.open(str(path), "rb") as reader:
            frame_rate = reader.getframerate()
            frame_count = reader.getnframes()
            channel_count = reader.getnchannels()
            sample_width = reader.getsampwidth()
            raw_audio = reader.readframes(frame_count)
    except wave.Error:
        return []
    if frame_rate <= 0 or frame_count <= 0 or channel_count <= 0 or sample_width not in {1, 2, 3, 4}:
        return []
    bytes_per_frame = channel_count * sample_width
    ranges = silence_ranges_for_audio(
        raw_audio,
        frame_count=frame_count,
        frame_rate=frame_rate,
        bytes_per_frame=bytes_per_frame,
        sample_width=sample_width,
        threshold_db=DEFAULT_SILENCE_THRESHOLD_DB,
        chunk_ms=DEFAULT_SILENCE_CHUNK_MS,
    )
    return [(start / frame_rate, end / frame_rate) for start, end, is_silence in ranges if not is_silence]


def snap_subtitle_segments_to_speech(
    audio_path: str | Path,
    segments: list[tuple[float, float, str]],
    offset_sec: float,
    *,
    snap_window_sec: float = DEFAULT_SUBTITLE_SPEECH_SNAP_WINDOW_SEC,
) -> list[tuple[float, float, str]]:
    speech_ranges = subtitle_speech_ranges(audio_path)
    if not speech_ranges or not segments:
        return segments

    offset = max(0.0, float(offset_sec or 0.0))
    snapped: list[tuple[float, float, str]] = []
    for start, end, text in segments:
        local_start = max(0.0, start - offset)
        snapped_start = start
        for speech_start, speech_end in speech_ranges:
            if speech_end <= local_start:
                continue
            if speech_start <= local_start < speech_end:
                break
            if 0 <= speech_start - local_start <= snap_window_sec:
                snapped_start = offset + speech_start
            break
        if end <= snapped_start:
            end = snapped_start + 0.1
        snapped.append((snapped_start, end, text))

    adjusted = snapped[:]
    for index in range(len(adjusted) - 1):
        start, end, text = adjusted[index]
        next_start = adjusted[index + 1][0]
        max_end = next_start - DEFAULT_SUBTITLE_OVERLAP_GAP_SEC
        if end > max_end:
            end = max(start + 0.1, max_end)
            adjusted[index] = (start, end, text)
    return adjusted


def align_subtitle_text_with_asr(
    audio_path: str | Path,
    text: str,
    offset_sec: float,
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    provider_name: str | None = None,
) -> list[tuple[float, float, str]]:
    chunks = split_subtitle_text(text)
    if not chunks:
        return []
    units = run_subtitle_alignment_asr(
        audio_path,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        provider_name=provider_name,
    )
    return align_subtitle_text_with_units(audio_path, chunks, units, offset_sec)


def align_subtitle_text_with_units(
    audio_path: str | Path,
    chunks: list[str],
    units: list[dict[str, Any]],
    offset_sec: float,
) -> list[tuple[float, float, str]]:
    if not units:
        raise ValueError(f"ASR 未识别到可对齐语音：{audio_path}")

    normalized_chunks = [normalize_subtitle_alignment_text(chunk) for chunk in chunks]
    normalized_lengths = [len(chunk) for chunk in normalized_chunks]
    expected_text = "".join(normalized_chunks)
    if not expected_text:
        return []

    expanded_units = [
        char_unit
        for unit in units
        for char_unit in _expand_asr_unit(
            float(unit.get("start") or 0.0),
            float(unit.get("end") or 0.0),
            safe_text(unit.get("text")),
        )
    ]
    recognized_text = "".join(safe_text(unit.get("text")) for unit in expanded_units)
    if not recognized_text:
        raise ValueError(f"ASR 未识别到可对齐文字：{audio_path}")

    matcher = SequenceMatcher(None, expected_text, recognized_text, autojunk=False)
    expected_to_unit: dict[int, int] = {}
    matched_chars = 0
    for block in matcher.get_matching_blocks():
        if block.size <= 0:
            continue
        matched_chars += block.size
        for delta in range(block.size):
            expected_to_unit[block.a + delta] = block.b + delta

    coverage = matched_chars / len(expected_text)
    if coverage < DEFAULT_SUBTITLE_ASR_MIN_COVERAGE:
        raise ValueError(
            "ASR 对齐质量不达标："
            f"文案覆盖率 {coverage:.1%}，最低要求 {DEFAULT_SUBTITLE_ASR_MIN_COVERAGE:.0%}；"
            f"文案 {len(expected_text)} 字，识别 {len(recognized_text)} 字，匹配 {matched_chars} 字。"
        )

    offset = max(0.0, float(offset_sec or 0.0))
    aligned: list[tuple[float, float, str]] = []
    expected_cursor = 0
    for index, (chunk, chunk_length) in enumerate(zip(chunks, normalized_lengths)):
        chunk_end = expected_cursor + chunk_length
        matched_unit_indexes = [
            expected_to_unit[position]
            for position in range(expected_cursor, chunk_end)
            if position in expected_to_unit
        ]
        if not matched_unit_indexes:
            raise ValueError(
                f"ASR 对齐质量不达标：第 {index + 1} 条字幕没有可信文字锚点（{chunk}）。"
            )
        start_unit = expanded_units[min(matched_unit_indexes)]
        end_unit = expanded_units[max(matched_unit_indexes)]
        start = offset + float(start_unit["start"])
        end = offset + float(end_unit["end"])
        if end <= start:
            end = start + 0.1
        aligned.append((start, end, chunk))
        expected_cursor = chunk_end
    return snap_subtitle_segments_to_speech(audio_path, aligned, offset)


def align_subtitle_jobs_with_asr(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    workers: int = DEFAULT_SUBTITLE_ASR_WORKERS,
    provider_name: str | None = None,
) -> list[tuple[float, float, str]]:
    grouped = align_subtitle_jobs_with_asr_grouped(
        jobs,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        workers=workers,
        provider_name=provider_name,
    )
    return [item for group in grouped for item in group]


def align_subtitle_jobs_with_asr_grouped(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    workers: int = DEFAULT_SUBTITLE_ASR_WORKERS,
    provider_name: str | None = None,
) -> list[list[tuple[float, float, str]]]:
    if not jobs:
        return []
    unit_results = run_subtitle_asr_worker(
        jobs,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        workers=workers,
        provider_name=provider_name,
    )
    grouped: list[list[tuple[float, float, str]]] = []
    for index, (job, units) in enumerate(zip(jobs, unit_results)):
        label = safe_text(job.get("label")) or f"字幕段 {index + 1}"
        chunks = split_subtitle_text(safe_text(job.get("text")))
        try:
            grouped.append(
                align_subtitle_text_with_units(
                    safe_text(job.get("audio_path")),
                    chunks,
                    units,
                    float(job.get("offset_sec") or 0.0),
                )
            )
        except Exception as exc:
            raise ValueError(f"{label} ASR 字幕对齐失败：{exc}") from exc
    return grouped


def format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def format_srt(items: list[tuple[float, float, str]]) -> str:
    lines: list[str] = []
    for index, (start, end, text) in enumerate(items, start=1):
        lines.extend(
            [
                str(index),
                f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}",
                text,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
