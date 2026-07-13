from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path
from typing import Any

from .forced_alignment import (
    DEFAULT_FORCED_ALIGNMENT_BATCH_SIZE,
    DEFAULT_FORCED_ALIGNMENT_LANGUAGE,
    DEFAULT_FORCED_ALIGNMENT_MODEL,
    align_subtitle_jobs_with_forced_alignment,
    align_subtitle_jobs_with_forced_alignment_grouped,
    forced_alignment_python_path,
)
from .utils import safe_text
from .subtitle_rules import (
    SUBTITLE_ALIGN_DROP_RE,
    SUBTITLE_BREAK_RE,
    SUBTITLE_DROP_PUNCT_RE,
    normalize_subtitle_alignment_text,
    split_subtitle_text,
)

# Public names stay stable for CLI compatibility. Their implementation is now
# transcript-constrained forced alignment, not free ASR transcription matching.
DEFAULT_SUBTITLE_ASR_MODEL = DEFAULT_FORCED_ALIGNMENT_MODEL
DEFAULT_SUBTITLE_ASR_LANGUAGE = DEFAULT_FORCED_ALIGNMENT_LANGUAGE
DEFAULT_SUBTITLE_ASR_BEAM_SIZE = 2
DEFAULT_SUBTITLE_ASR_WORKERS = DEFAULT_FORCED_ALIGNMENT_BATCH_SIZE
DEFAULT_SUBTITLE_SPEECH_SNAP_WINDOW_SEC = 0.5
DEFAULT_SUBTITLE_OVERLAP_GAP_SEC = 0.02


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


def subtitle_asr_python_path() -> Path:
    return forced_alignment_python_path()


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
    return align_subtitle_jobs_with_forced_alignment(
        [{"audio_path": str(audio_path), "text": text, "offset_sec": offset_sec}],
        model_name=model_name,
        language=language,
        batch_size=1,
    )


def align_subtitle_jobs_with_asr(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    workers: int = DEFAULT_SUBTITLE_ASR_WORKERS,
    provider_name: str | None = None,
) -> list[tuple[float, float, str]]:
    return align_subtitle_jobs_with_forced_alignment(
        jobs,
        model_name=model_name,
        language=language,
        batch_size=workers,
    )


def align_subtitle_jobs_with_asr_grouped(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    workers: int = DEFAULT_SUBTITLE_ASR_WORKERS,
    provider_name: str | None = None,
) -> list[list[tuple[float, float, str]]]:
    return align_subtitle_jobs_with_forced_alignment_grouped(
        jobs,
        model_name=model_name,
        language=language,
        batch_size=workers,
    )


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
