from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..audio_sources import resolve_cloud_audio_source
from ...subtitle_rules import normalize_subtitle_alignment_text
from ...utils import safe_text
from .base import AsrProvider


DEFAULT_DOUBAO_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
DEFAULT_DOUBAO_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
DEFAULT_DOUBAO_RESOURCE_ID = "volc.seedasr.auc"
DEFAULT_DOUBAO_MODEL_NAME = "bigmodel"
DEFAULT_DOUBAO_POLL_INTERVAL_SEC = 1.0
DEFAULT_DOUBAO_MAX_POLL_SEC = 900.0
DEFAULT_DOUBAO_HTTP_TIMEOUT_SEC = 60.0
PENDING_STATUS_CODES = {"20000001", "20000002", "55000001"}
SUCCESS_STATUS_CODE = "20000000"


class DoubaoProvider(AsrProvider):
    name = "doubao"

    def __init__(
        self,
        *,
        session: Any | None = None,
        request_id_factory: Callable[[], str] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._session = session
        self._request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))
        self._sleep = sleep or time.sleep

    @property
    def session(self) -> Any:
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

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
        return [
            _result_to_units(self._recognize_job(job, language=language))
            for job in jobs
        ]

    def transcribe_segments(
        self,
        audio_path: str | Path,
        *,
        model_name: str,
        language: str,
        beam_size: int,
        vad_filter: bool = True,
    ) -> list[dict[str, Any]]:
        return _result_to_segments(self._recognize_job({"audio_path": str(audio_path)}, language=language))

    def _recognize_job(self, job: dict[str, Any], *, language: str) -> dict[str, Any]:
        audio_source = resolve_cloud_audio_source(
            job,
            env_prefix="BWORKFLOW_DOUBAO_ASR",
            provider_label="Doubao ASR",
        )
        request_id = self._request_id_factory()
        headers = self._headers(request_id, submit=True)
        payload = self._payload(audio_source.url, audio_format=audio_source.format, language=language)
        submit_response = self.session.post(
            _env("BWORKFLOW_DOUBAO_ASR_SUBMIT_URL", DEFAULT_DOUBAO_SUBMIT_URL),
            json=payload,
            headers=headers,
            timeout=_env_float("BWORKFLOW_DOUBAO_ASR_HTTP_TIMEOUT_SEC", DEFAULT_DOUBAO_HTTP_TIMEOUT_SEC),
        )
        _raise_for_http(submit_response)
        submit_status = _response_header(submit_response, "X-Api-Status-Code")
        submit_logid = _response_header(submit_response, "X-Tt-Logid")
        if submit_status and submit_status not in {SUCCESS_STATUS_CODE, *PENDING_STATUS_CODES}:
            raise ValueError(_status_error("Doubao ASR submit failed", submit_response, request_id, submit_logid))

        return self._poll_result(request_id, submit_logid)

    def _poll_result(self, request_id: str, logid: str) -> dict[str, Any]:
        deadline = time.monotonic() + _env_float("BWORKFLOW_DOUBAO_ASR_MAX_POLL_SEC", DEFAULT_DOUBAO_MAX_POLL_SEC)
        poll_interval = _env_float("BWORKFLOW_DOUBAO_ASR_POLL_INTERVAL_SEC", DEFAULT_DOUBAO_POLL_INTERVAL_SEC)
        headers = self._headers(request_id, submit=False, logid=logid)
        query_url = _env("BWORKFLOW_DOUBAO_ASR_QUERY_URL", DEFAULT_DOUBAO_QUERY_URL)
        timeout = _env_float("BWORKFLOW_DOUBAO_ASR_HTTP_TIMEOUT_SEC", DEFAULT_DOUBAO_HTTP_TIMEOUT_SEC)
        last_status = ""
        last_message = ""

        while time.monotonic() <= deadline:
            response = self.session.post(query_url, json={}, headers=headers, timeout=timeout)
            _raise_for_http(response)
            status = _response_header(response, "X-Api-Status-Code")
            last_status = status
            last_message = _response_header(response, "X-Api-Message")
            if status == SUCCESS_STATUS_CODE:
                payload = _response_json(response)
                result = _extract_result(payload)
                if not result:
                    raise ValueError(
                        f"Doubao ASR returned success without result: request_id={request_id}, logid={logid}"
                    )
                return result
            if status in PENDING_STATUS_CODES or not status:
                self._sleep(max(0.0, poll_interval))
                continue
            raise ValueError(_status_error("Doubao ASR query failed", response, request_id, logid))

        raise ValueError(
            "Doubao ASR query timed out: "
            f"request_id={request_id}, logid={logid}, last_status={last_status}, last_message={last_message}"
        )

    def _headers(self, request_id: str, *, submit: bool, logid: str = "") -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": _env("BWORKFLOW_DOUBAO_ASR_RESOURCE_ID", DEFAULT_DOUBAO_RESOURCE_ID),
            "X-Api-Request-Id": request_id,
        }
        api_key = _first_env("BWORKFLOW_DOUBAO_ASR_API_KEY", "DOUBAO_ASR_API_KEY", "VOLCENGINE_DOUBAO_ASR_API_KEY")
        if api_key:
            headers["X-Api-Key"] = api_key
        else:
            app_key = _first_env("BWORKFLOW_DOUBAO_ASR_APP_KEY", "DOUBAO_ASR_APP_KEY", "VOLCENGINE_ASR_APP_KEY")
            access_key = _first_env(
                "BWORKFLOW_DOUBAO_ASR_ACCESS_KEY",
                "DOUBAO_ASR_ACCESS_KEY",
                "VOLCENGINE_ASR_ACCESS_KEY",
            )
            if not app_key or not access_key:
                raise ValueError(
                    "Doubao ASR credentials are missing. Set BWORKFLOW_DOUBAO_ASR_API_KEY, "
                    "or set BWORKFLOW_DOUBAO_ASR_APP_KEY and BWORKFLOW_DOUBAO_ASR_ACCESS_KEY."
                )
            headers["X-Api-App-Key"] = app_key
            headers["X-Api-Access-Key"] = access_key
        if submit:
            headers["X-Api-Sequence"] = "-1"
        if logid:
            headers["X-Tt-Logid"] = logid
        return headers

    def _payload(self, audio_url: str, *, audio_format: str, language: str) -> dict[str, Any]:
        return {
            "user": {"uid": _env("BWORKFLOW_DOUBAO_ASR_UID", "bworkflow")},
            "audio": {
                "url": audio_url,
                "format": audio_format,
                "language": _language_code(language),
            },
            "request": {
                "model_name": _env("BWORKFLOW_DOUBAO_ASR_MODEL", DEFAULT_DOUBAO_MODEL_NAME),
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_speaker_info": False,
            },
        }


def _env(name: str, default: str) -> str:
    return safe_text(os.environ.get(name)) or default


def _first_env(*names: str) -> str:
    for name in names:
        value = safe_text(os.environ.get(name))
        if value:
            return value
    return ""


def _env_float(name: str, default: float) -> float:
    try:
        return float(safe_text(os.environ.get(name)) or default)
    except ValueError:
        return default


def _language_code(language: str) -> str:
    value = safe_text(language).casefold().replace("_", "-")
    if value in {"zh", "cn", "zh-cn", "zh-hans"}:
        return "zh-CN"
    if value in {"en", "en-us"}:
        return "en-US"
    if value in {"ja", "jp", "ja-jp"}:
        return "ja-JP"
    if value in {"ko", "kr", "ko-kr"}:
        return "ko-KR"
    return safe_text(language) or "zh-CN"


def _response_header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    return safe_text(headers.get(name) or headers.get(name.lower()) or headers.get(name.upper()))


def _raise_for_http(response: Any) -> None:
    try:
        response.raise_for_status()
    except Exception as exc:
        text = safe_text(getattr(response, "text", ""))
        status_code = safe_text(getattr(response, "status_code", ""))
        detail = f"HTTP {status_code}".strip()
        raise ValueError(f"Doubao ASR HTTP request failed: {detail} {text}".strip()) from exc


def _response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise ValueError(f"Doubao ASR returned non-JSON response: {safe_text(getattr(response, 'text', ''))}") from exc
    return payload if isinstance(payload, dict) else {}


def _status_error(prefix: str, response: Any, request_id: str, logid: str) -> str:
    status = _response_header(response, "X-Api-Status-Code")
    message = _response_header(response, "X-Api-Message")
    text = safe_text(getattr(response, "text", ""))
    return f"{prefix}: status={status}, message={message}, request_id={request_id}, logid={logid}, body={text}"


def _extract_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                return item
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        return data["result"]
    if isinstance(data, dict) and isinstance(data.get("result"), list):
        for item in data["result"]:
            if isinstance(item, dict):
                return item
    return {}


def _result_to_units(result: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for utterance in _utterances(result):
        words = utterance.get("words")
        if isinstance(words, list) and words:
            for word in words:
                if isinstance(word, dict):
                    units.extend(
                        _expand_timed_text(
                            _seconds_from_ms(word.get("start_time")),
                            _seconds_from_ms(word.get("end_time")),
                            safe_text(word.get("text")),
                        )
                    )
            continue
        units.extend(
            _expand_timed_text(
                _seconds_from_ms(utterance.get("start_time")),
                _seconds_from_ms(utterance.get("end_time")),
                safe_text(utterance.get("text")),
            )
        )
    if units:
        return units
    return _expand_timed_text(0.0, _duration_seconds(result), safe_text(result.get("text")))


def _result_to_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for utterance in _utterances(result):
        text = safe_text(utterance.get("text"))
        if not text:
            continue
        start = _seconds_from_ms(utterance.get("start_time"))
        end = _seconds_from_ms(utterance.get("end_time"))
        if end <= start:
            end = start + 0.001
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    if segments:
        return segments
    text = safe_text(result.get("text"))
    return [{"start": 0.0, "end": round(_duration_seconds(result), 3), "text": text}] if text else []


def _utterances(result: dict[str, Any]) -> list[dict[str, Any]]:
    utterances = result.get("utterances")
    if not isinstance(utterances, list):
        return []
    return [utterance for utterance in utterances if isinstance(utterance, dict)]


def _seconds_from_ms(value: Any) -> float:
    try:
        return max(0.0, float(value or 0) / 1000.0)
    except (TypeError, ValueError):
        return 0.0


def _duration_seconds(result: dict[str, Any]) -> float:
    additions = result.get("additions")
    if isinstance(additions, dict):
        duration = _seconds_from_ms(additions.get("duration"))
        if duration > 0:
            return duration
    return 0.001


def _expand_timed_text(start: float, end: float, text: str) -> list[dict[str, Any]]:
    clean = normalize_subtitle_alignment_text(text)
    if not clean:
        return []
    start = max(0.0, float(start or 0.0))
    end = max(start + 0.001, float(end or start))
    step = (end - start) / len(clean)
    return [
        {
            "start": round(start + step * index, 3),
            "end": round(start + step * (index + 1), 3),
            "text": char,
        }
        for index, char in enumerate(clean)
    ]
