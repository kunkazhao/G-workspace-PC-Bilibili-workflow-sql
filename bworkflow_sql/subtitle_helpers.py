from __future__ import annotations

import json
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

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
DEFAULT_SUBTITLE_ASR_PYTHON = Path(__file__).resolve().parents[1] / ".venv-asr" / "Scripts" / "python.exe"
DEFAULT_SUBTITLE_ASR_WORKER = Path(__file__).resolve().parents[1] / "scripts" / "subtitle_asr_worker.py"


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
    configured = safe_text(os.environ.get("BWORKFLOW_ASR_PYTHON"))
    return Path(configured) if configured else DEFAULT_SUBTITLE_ASR_PYTHON


def run_subtitle_asr_worker(
    jobs: list[dict[str, Any]],
    *,
    model_name: str,
    language: str,
    beam_size: int,
    workers: int,
) -> list[list[dict[str, Any]]]:
    python_exe = subtitle_asr_python_path()
    if not python_exe.exists():
        raise ValueError(
            f"独立 ASR 环境不存在：{python_exe}\n"
            "请运行 scripts\\setup_subtitle_asr.ps1 安装项目专用 Python 3.11 环境。"
        )
    if not DEFAULT_SUBTITLE_ASR_WORKER.exists():
        raise ValueError(f"ASR 子进程脚本不存在：{DEFAULT_SUBTITLE_ASR_WORKER}")

    payload = {
        "model_name": model_name,
        "language": language,
        "beam_size": max(1, int(beam_size or 1)),
        "cpu_threads": max(1, int(workers or 1)),
        "jobs": [{"audio_path": safe_text(job.get("audio_path"))} for job in jobs],
    }
    with tempfile.TemporaryDirectory(prefix="bworkflow-asr-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        response_path = Path(temp_dir) / "response.json"
        request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        completed = subprocess.run(
            [str(python_exe), str(DEFAULT_SUBTITLE_ASR_WORKER), str(request_path), str(response_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            creationflags=creationflags,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(f"独立 ASR 子进程失败（退出码 {completed.returncode}）：{detail or '没有错误输出'}")
        if not response_path.exists():
            raise ValueError("独立 ASR 子进程没有生成结果文件。")
        response = json.loads(response_path.read_text(encoding="utf-8-sig"))
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise ValueError("独立 ASR 子进程返回格式无效。")
    results = response["results"]
    if len(results) != len(jobs):
        raise ValueError(f"独立 ASR 返回条数不匹配：任务 {len(jobs)}，结果 {len(results)}。")
    return [result if isinstance(result, list) else [] for result in results]


def run_subtitle_alignment_asr(
    audio_path: str | Path,
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
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
) -> list[tuple[float, float, str]]:
    chunks = split_subtitle_text(text)
    if not chunks:
        return []
    units = run_subtitle_alignment_asr(audio_path, model_name=model_name, language=language, beam_size=beam_size)
    return align_subtitle_text_with_units(audio_path, chunks, units, offset_sec)


def align_subtitle_text_with_units(
    audio_path: str | Path,
    chunks: list[str],
    units: list[dict[str, Any]],
    offset_sec: float,
) -> list[tuple[float, float, str]]:
    if not units:
        raise ValueError(f"ASR 未识别到可对齐语音：{audio_path}")

    normalized_lengths = [len(normalize_subtitle_alignment_text(chunk)) for chunk in chunks]
    if sum(normalized_lengths) <= 0:
        return []

    unit_index = 0
    offset = max(0.0, float(offset_sec or 0.0))
    aligned: list[tuple[float, float, str]] = []
    for index, chunk in enumerate(chunks):
        remaining_chunks = len(chunks) - index - 1
        available = len(units) - unit_index
        if available <= 0:
            start = aligned[-1][1] if aligned else offset
            aligned.append((start, start + 0.1, chunk))
            continue
        if index == len(chunks) - 1:
            take = available
        else:
            target_len = max(normalized_lengths[index], 1)
            take = min(target_len, max(1, available - remaining_chunks))
        start_unit = units[unit_index]
        end_unit = units[min(len(units) - 1, unit_index + take - 1)]
        start = offset + float(start_unit["start"])
        end = offset + float(end_unit["end"])
        if end <= start:
            end = start + 0.1
        aligned.append((start, end, chunk))
        unit_index += take
    return snap_subtitle_segments_to_speech(audio_path, aligned, offset)


def align_subtitle_jobs_with_asr(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_SUBTITLE_ASR_MODEL,
    language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
    beam_size: int = DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    workers: int = DEFAULT_SUBTITLE_ASR_WORKERS,
) -> list[tuple[float, float, str]]:
    if not jobs:
        return []
    unit_results = run_subtitle_asr_worker(
        jobs,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        workers=workers,
    )
    merged: list[tuple[float, float, str]] = []
    for index, (job, units) in enumerate(zip(jobs, unit_results)):
        label = safe_text(job.get("label")) or f"字幕段 {index + 1}"
        chunks = split_subtitle_text(safe_text(job.get("text")))
        try:
            merged.extend(
                align_subtitle_text_with_units(
                    safe_text(job.get("audio_path")),
                    chunks,
                    units,
                    float(job.get("offset_sec") or 0.0),
                )
            )
        except Exception as exc:
            raise ValueError(f"{label} ASR 字幕对齐失败：{exc}") from exc
    return merged


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
