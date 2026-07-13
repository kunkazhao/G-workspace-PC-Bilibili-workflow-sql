from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1


def probe_duration(audio_path: str) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            audio_path,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"无法读取音频时长：{audio_path}: {completed.stderr.strip()}")
    payload = json.loads(completed.stdout or "{}")
    duration = float(payload.get("format", {}).get("duration") or 0.0)
    if duration <= 0:
        raise ValueError(f"音频时长无效：{audio_path}")
    return duration


def align_batch(model: Any, jobs: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    results = model.align(
        audio=[str(job.get("audio_path") or "") for job in jobs],
        text=[str(job.get("text") or "") for job in jobs],
        language=[language] * len(jobs),
    )
    aligned: list[dict[str, Any]] = []
    for job, result in zip(jobs, results):
        aligned.append(
            {
                "audio_duration_sec": probe_duration(str(job.get("audio_path") or "")),
                "items": [
                    {
                        "text": item.text,
                        "start": float(item.start_time),
                        "end": float(item.end_time),
                    }
                    for item in result
                ],
            }
        )
    return aligned


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: subtitle_forced_alignment_worker.py REQUEST_JSON RESPONSE_JSON", file=sys.stderr)
        return 2
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    payload = json.loads(request_path.read_text(encoding="utf-8-sig"))
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported forced-alignment protocol version")

    import torch
    from qwen_asr import Qwen3ForcedAligner

    use_cuda = torch.cuda.is_available()
    model = Qwen3ForcedAligner.from_pretrained(
        str(payload.get("model_name") or "Qwen/Qwen3-ForcedAligner-0.6B"),
        dtype=torch.bfloat16 if use_cuda else torch.float32,
        device_map="cuda:0" if use_cuda else "cpu",
    )
    jobs = payload.get("jobs") or []
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list")
    batch_size = max(1, int(payload.get("batch_size") or 1))
    language = str(payload.get("language") or "Chinese")
    results: list[dict[str, Any]] = []
    for start in range(0, len(jobs), batch_size):
        results.extend(align_batch(model, jobs[start : start + batch_size], language))
    response_path.write_text(
        json.dumps(
            {"protocol_version": PROTOCOL_VERSION, "results": results},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
