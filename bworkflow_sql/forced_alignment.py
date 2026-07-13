from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .settings import INTERNAL_WORKSPACE_ROOT
from .subtitle_rules import normalize_subtitle_alignment_text, split_subtitle_text
from .utils import safe_text


DEFAULT_FORCED_ALIGNMENT_MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
DEFAULT_FORCED_ALIGNMENT_MODEL_ROOT = Path(__file__).resolve().parents[1] / "data" / "models" / "Qwen3-ForcedAligner-0.6B"
DEFAULT_FORCED_ALIGNMENT_MODEL = (
    str(DEFAULT_FORCED_ALIGNMENT_MODEL_ROOT)
    if (DEFAULT_FORCED_ALIGNMENT_MODEL_ROOT / "config.json").is_file()
    else DEFAULT_FORCED_ALIGNMENT_MODEL_ID
)
DEFAULT_FORCED_ALIGNMENT_LANGUAGE = "Chinese"
DEFAULT_FORCED_ALIGNMENT_BATCH_SIZE = 4
DEFAULT_FORCED_ALIGNMENT_PYTHON = Path(__file__).resolve().parents[1] / ".venv-align" / "Scripts" / "python.exe"
DEFAULT_FORCED_ALIGNMENT_WORKER = Path(__file__).resolve().parents[1] / "scripts" / "subtitle_forced_alignment_worker.py"
FORCED_ALIGNMENT_CACHE_ROOT = INTERNAL_WORKSPACE_ROOT / "subtitle-forced-alignment-cache"
FORCED_ALIGNMENT_CACHE_SCHEMA = 1
FORCED_ALIGNMENT_PROTOCOL_VERSION = 1
FORCED_ALIGNMENT_MAX_EDGE_SILENCE_SEC = 2.0
FORCED_ALIGNMENT_MIN_SUBTITLE_DURATION_SEC = 0.08
FORCED_ALIGNMENT_TIME_EPSILON_SEC = 0.02


def forced_alignment_python_path() -> Path:
    configured = safe_text(os.environ.get("BWORKFLOW_FORCED_ALIGNMENT_PYTHON"))
    return Path(configured) if configured else DEFAULT_FORCED_ALIGNMENT_PYTHON


def _cache_key(job: dict[str, Any], *, model_name: str, language: str) -> str:
    audio_path = Path(safe_text(job.get("audio_path"))).resolve()
    digest = hashlib.sha256()
    with audio_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    digest.update(
        json.dumps(
            {
                "text": safe_text(job.get("text")),
                "model": model_name,
                "language": language,
                "split_rule": "subtitle-rules-v3",
                "protocol": FORCED_ALIGNMENT_PROTOCOL_VERSION,
                "cache_schema": FORCED_ALIGNMENT_CACHE_SCHEMA,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _load_cached_result(cache_key: str) -> dict[str, Any] | None:
    path = FORCED_ALIGNMENT_CACHE_ROOT / f"{cache_key}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else None


def _store_cached_result(cache_key: str, result: dict[str, Any]) -> None:
    FORCED_ALIGNMENT_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    target = FORCED_ALIGNMENT_CACHE_ROOT / f"{cache_key}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"schemaVersion": FORCED_ALIGNMENT_CACHE_SCHEMA, "result": result},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(target)


def run_forced_alignment_worker(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_FORCED_ALIGNMENT_MODEL,
    language: str = DEFAULT_FORCED_ALIGNMENT_LANGUAGE,
    batch_size: int = DEFAULT_FORCED_ALIGNMENT_BATCH_SIZE,
) -> list[dict[str, Any]]:
    python_exe = forced_alignment_python_path()
    if not python_exe.is_file():
        raise ValueError(
            f"独立强制对齐环境不存在：{python_exe}\n"
            "请运行 scripts\\setup_subtitle_forced_alignment.ps1 安装项目专用环境。"
        )
    if not DEFAULT_FORCED_ALIGNMENT_WORKER.is_file():
        raise ValueError(f"强制对齐子进程脚本不存在：{DEFAULT_FORCED_ALIGNMENT_WORKER}")

    payload = {
        "protocol_version": FORCED_ALIGNMENT_PROTOCOL_VERSION,
        "model_name": model_name,
        "language": language,
        "batch_size": max(1, int(batch_size or 1)),
        "jobs": [
            {
                "audio_path": safe_text(job.get("audio_path")),
                "text": safe_text(job.get("text")),
            }
            for job in jobs
        ],
    }
    with tempfile.TemporaryDirectory(prefix="bworkflow-forced-alignment-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        response_path = Path(temp_dir) / "response.json"
        request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        worker_env = os.environ.copy()
        worker_env.setdefault("HF_HUB_DISABLE_XET", "1")
        completed = subprocess.run(
            [str(python_exe), str(DEFAULT_FORCED_ALIGNMENT_WORKER), str(request_path), str(response_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            creationflags=creationflags,
            env=worker_env,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(f"强制对齐子进程失败（退出码 {completed.returncode}）：{detail or '没有错误输出'}")
        if not response_path.is_file():
            raise ValueError("强制对齐子进程没有生成结果文件。")
        response = json.loads(response_path.read_text(encoding="utf-8-sig"))

    if not isinstance(response, dict) or response.get("protocol_version") != FORCED_ALIGNMENT_PROTOCOL_VERSION:
        raise ValueError("强制对齐子进程返回协议无效。")
    results = response.get("results")
    if not isinstance(results, list) or len(results) != len(jobs):
        count = len(results) if isinstance(results, list) else 0
        raise ValueError(f"强制对齐返回条数不匹配：任务 {len(jobs)}，结果 {count}。")
    if not all(isinstance(result, dict) for result in results):
        raise ValueError("强制对齐子进程返回结果格式无效。")
    return results


def _expand_items(items: list[dict[str, Any]], audio_duration_sec: float) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, item in enumerate(items, start=1):
        text = normalize_subtitle_alignment_text(item.get("text"))
        if not text:
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {index} 个强制对齐锚点时间无效。") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError(f"第 {index} 个强制对齐锚点时间无效：{start}-{end}。")
        if start + FORCED_ALIGNMENT_TIME_EPSILON_SEC < previous_end:
            raise ValueError(f"第 {index} 个强制对齐锚点发生倒序或重叠。")
        start = max(start, previous_end)
        if end > audio_duration_sec + FORCED_ALIGNMENT_TIME_EPSILON_SEC:
            raise ValueError(f"第 {index} 个强制对齐锚点超出音频时长。")
        step = (end - start) / len(text)
        for char_index, char in enumerate(text):
            expanded.append(
                {
                    "text": char,
                    "start": start + step * char_index,
                    "end": start + step * (char_index + 1),
                }
            )
        previous_end = end
    return expanded


def items_to_subtitle_segments(
    chunks: list[str],
    items: list[dict[str, Any]],
    *,
    offset_sec: float,
    audio_duration_sec: float,
) -> list[tuple[float, float, str]]:
    if not chunks:
        return []
    if not math.isfinite(audio_duration_sec) or audio_duration_sec <= 0:
        raise ValueError("强制对齐没有返回有效音频时长。")

    normalized_chunks = [normalize_subtitle_alignment_text(chunk) for chunk in chunks]
    expected_text = "".join(normalized_chunks)
    expanded = _expand_items(items, audio_duration_sec)
    aligned_text = "".join(safe_text(item.get("text")) for item in expanded)
    if not expanded or aligned_text != expected_text:
        raise ValueError(
            "强制对齐结果没有完整覆盖精确原文："
            f"原文 {len(expected_text)} 字，对齐 {len(aligned_text)} 字。"
        )
    if expanded[0]["start"] > FORCED_ALIGNMENT_MAX_EDGE_SILENCE_SEC:
        raise ValueError(f"首个文字锚点距音频起点 {expanded[0]['start']:.2f} 秒，疑似漏掉开头。")
    trailing_silence = audio_duration_sec - float(expanded[-1]["end"])
    if trailing_silence > FORCED_ALIGNMENT_MAX_EDGE_SILENCE_SEC:
        raise ValueError(f"末个文字锚点距音频结尾 {trailing_silence:.2f} 秒，疑似漏掉结尾。")

    offset = max(0.0, float(offset_sec or 0.0))
    result: list[tuple[float, float, str]] = []
    cursor = 0
    for index, (chunk, normalized) in enumerate(zip(chunks, normalized_chunks), start=1):
        next_cursor = cursor + len(normalized)
        chunk_items = expanded[cursor:next_cursor]
        if not chunk_items:
            raise ValueError(f"第 {index} 条字幕没有强制对齐锚点：{chunk}")
        start = offset + float(chunk_items[0]["start"])
        end = offset + float(chunk_items[-1]["end"])
        if end - start < FORCED_ALIGNMENT_MIN_SUBTITLE_DURATION_SEC:
            raise ValueError(f"第 {index} 条字幕显示时间过短：{end - start:.3f} 秒。")
        if result and start + FORCED_ALIGNMENT_TIME_EPSILON_SEC < result[-1][1]:
            raise ValueError(f"第 {index} 条字幕与上一条发生重叠。")
        if result:
            start = max(start, result[-1][1])
        result.append((start, end, chunk))
        cursor = next_cursor
    if cursor != len(expanded):
        raise ValueError("强制对齐锚点未被字幕完整消费。")
    return result


def forced_alignment_results(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_FORCED_ALIGNMENT_MODEL,
    language: str = DEFAULT_FORCED_ALIGNMENT_LANGUAGE,
    batch_size: int = DEFAULT_FORCED_ALIGNMENT_BATCH_SIZE,
) -> list[dict[str, Any]]:
    if not jobs:
        return []
    results: list[dict[str, Any] | None] = [None] * len(jobs)
    cache_keys: list[str] = []
    missing_jobs: list[dict[str, Any]] = []
    missing_indexes: list[int] = []
    for index, job in enumerate(jobs):
        audio_path = Path(safe_text(job.get("audio_path")))
        if not audio_path.is_file():
            raise ValueError(f"字幕音频不存在：{audio_path}")
        cache_key = _cache_key(job, model_name=model_name, language=language)
        cache_keys.append(cache_key)
        cached = _load_cached_result(cache_key)
        if cached is None:
            missing_jobs.append(job)
            missing_indexes.append(index)
        else:
            results[index] = cached

    if missing_jobs:
        fresh_results = run_forced_alignment_worker(
            missing_jobs,
            model_name=model_name,
            language=language,
            batch_size=batch_size,
        )
        for result_index, fresh in zip(missing_indexes, fresh_results):
            results[result_index] = fresh
            _store_cached_result(cache_keys[result_index], fresh)

    validated: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        label = safe_text(jobs[index].get("label")) or f"字幕段 {index + 1}"
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise ValueError(f"{label}: 强制对齐结果缺少文字锚点。")
        validated.append(result)
    return validated


def align_subtitle_jobs_with_forced_alignment_grouped(
    jobs: list[dict[str, Any]],
    *,
    model_name: str = DEFAULT_FORCED_ALIGNMENT_MODEL,
    language: str = DEFAULT_FORCED_ALIGNMENT_LANGUAGE,
    batch_size: int = DEFAULT_FORCED_ALIGNMENT_BATCH_SIZE,
) -> list[list[tuple[float, float, str]]]:
    if not jobs:
        return []
    results = forced_alignment_results(
        jobs,
        model_name=model_name,
        language=language,
        batch_size=batch_size,
    )

    grouped: list[list[tuple[float, float, str]]] = []
    errors: list[str] = []
    for index, (job, result) in enumerate(zip(jobs, results)):
        label = safe_text(job.get("label")) or f"字幕段 {index + 1}"
        try:
            grouped.append(
                items_to_subtitle_segments(
                    split_subtitle_text(job.get("text")),
                    result["items"],
                    offset_sec=float(job.get("offset_sec") or 0.0),
                    audio_duration_sec=float(result.get("audio_duration_sec") or 0.0),
                )
            )
        except Exception as exc:
            grouped.append([])
            errors.append(f"{label}: {exc}")
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"强制对齐字幕预检失败（{len(errors)} 段）：\n{details}")
    return grouped


def align_subtitle_jobs_with_forced_alignment(
    jobs: list[dict[str, Any]],
    **kwargs: Any,
) -> list[tuple[float, float, str]]:
    grouped = align_subtitle_jobs_with_forced_alignment_grouped(jobs, **kwargs)
    return [item for group in grouped for item in group]
