from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ...utils import safe_text
from .base import AsrProvider


DEFAULT_ASR_PYTHON = Path(__file__).resolve().parents[3] / ".venv-asr" / "Scripts" / "python.exe"
DEFAULT_ASR_WORKER = Path(__file__).resolve().parents[3] / "scripts" / "subtitle_asr_worker.py"


class FasterWhisperProvider(AsrProvider):
    name = "faster_whisper"

    def python_path(self) -> Path:
        configured = safe_text(os.environ.get("BWORKFLOW_ASR_PYTHON"))
        return Path(configured) if configured else DEFAULT_ASR_PYTHON

    def transcribe_units(
        self,
        jobs: list[dict[str, Any]],
        *,
        model_name: str,
        language: str,
        beam_size: int,
        workers: int,
        vad_filter: bool = False,
    ) -> list[list[dict[str, Any]]]:
        if vad_filter:
            raise ValueError("faster_whisper unit alignment does not support VAD in the shared worker")
        python_exe = self.python_path()
        if not python_exe.exists():
            raise ValueError(
                f"独立 ASR 环境不存在：{python_exe}\n"
                "请运行 scripts\\setup_subtitle_asr.ps1 安装项目专用 Python 3.11 环境。"
            )
        if not DEFAULT_ASR_WORKER.exists():
            raise ValueError(f"ASR 子进程脚本不存在：{DEFAULT_ASR_WORKER}")

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
                [str(python_exe), str(DEFAULT_ASR_WORKER), str(request_path), str(response_path)],
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

    def transcribe_segments(
        self,
        audio_path: str | Path,
        *,
        model_name: str,
        language: str,
        beam_size: int,
        vad_filter: bool = True,
    ) -> list[dict[str, Any]]:
        code = """
import json
import sys
from faster_whisper import WhisperModel

audio_path = sys.argv[1]
model_name = sys.argv[2]
language = sys.argv[3]
beam_size = max(1, int(sys.argv[4]))
vad_filter = sys.argv[5] == "1"

model = WhisperModel(model_name, device="cpu", compute_type="int8")
segments, _info = model.transcribe(
    audio_path,
    language=language,
    vad_filter=vad_filter,
    word_timestamps=True,
    beam_size=beam_size,
)

payload = []
for seg in segments:
    payload.append(
        {
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
        }
    )

print(json.dumps(payload, ensure_ascii=False))
""".strip()
        audio = Path(audio_path)
        with tempfile.TemporaryDirectory(prefix="bworkflow-asr-segments-") as temp_dir:
            temp_audio = Path(temp_dir) / f"audio{audio.suffix.lower()}"
            shutil.copy2(audio, temp_audio)
            completed = subprocess.run(
                [
                    str(self.python_path()),
                    "-c",
                    code,
                    str(temp_audio),
                    model_name,
                    language,
                    str(max(1, int(beam_size or 1))),
                    "1" if vad_filter else "0",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
        response = json.loads(completed.stdout or "[]")
        return response if isinstance(response, list) else []
