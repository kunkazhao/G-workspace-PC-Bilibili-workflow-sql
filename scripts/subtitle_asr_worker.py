from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

DROP_RE = re.compile(r"[\s，,。.!！?？；;：:、/\\\-—_~·`\"“”'‘’（）()【】\[\]{}《》<>]+|……|…")


def expand_unit(start: float, end: float, text: str) -> list[dict[str, Any]]:
    clean = DROP_RE.sub("", str(text or "")).casefold()
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


def transcribe(model: Any, audio_path: str, *, language: str, beam_size: int) -> list[dict[str, Any]]:
    segments, _info = model.transcribe(
        audio_path,
        language=language or None,
        vad_filter=False,
        word_timestamps=True,
        beam_size=max(1, beam_size),
    )
    units: list[dict[str, Any]] = []
    for segment in segments:
        words = getattr(segment, "words", None) or []
        if words:
            for word in words:
                units.extend(expand_unit(word.start, word.end, word.word))
        else:
            units.extend(expand_unit(segment.start, segment.end, segment.text))
    return units


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: subtitle_asr_worker.py REQUEST_JSON RESPONSE_JSON", file=sys.stderr)
        return 2
    request_path = Path(sys.argv[1])
    response_path = Path(sys.argv[2])
    payload = json.loads(request_path.read_text(encoding="utf-8-sig"))

    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(payload.get("model_name") or "base"),
        device="cpu",
        compute_type="int8",
        cpu_threads=max(1, int(payload.get("cpu_threads") or 1)),
        num_workers=1,
    )
    language = str(payload.get("language") or "")
    beam_size = max(1, int(payload.get("beam_size") or 1))
    results = [
        transcribe(model, str(job.get("audio_path") or ""), language=language, beam_size=beam_size)
        for job in payload.get("jobs") or []
    ]
    response_path.write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
