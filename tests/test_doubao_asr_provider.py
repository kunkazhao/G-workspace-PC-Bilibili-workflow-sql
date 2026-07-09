from __future__ import annotations

from pathlib import Path
from typing import Any


class FakeResponse:
    def __init__(self, *, headers: dict[str, str], payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.headers = headers
        self._payload = payload or {}
        self.text = text
        self.status_code = 200
        self.content = b"{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_doubao_transcribe_units_submits_audio_url_and_converts_word_timings(monkeypatch, tmp_path: Path):
    from bworkflow_sql.asr.providers.doubao import DoubaoProvider

    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_APP_KEY", "app-key")
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_ACCESS_KEY", "access-key")
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_POLL_INTERVAL_SEC", "0")

    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wave")
    calls: list[dict[str, Any]] = []

    class FakeSession:
        def post(self, url: str, *, json: Any, headers: dict[str, str], timeout: float):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            if url.endswith("/submit"):
                return FakeResponse(
                    headers={
                        "X-Api-Status-Code": "20000000",
                        "X-Api-Message": "OK",
                        "X-Tt-Logid": "log-1",
                    }
                )
            return FakeResponse(
                headers={"X-Api-Status-Code": "20000000", "X-Api-Message": "OK"},
                payload={
                    "result": {
                        "text": "你好世界",
                        "utterances": [
                            {
                                "text": "你好世界",
                                "start_time": 0,
                                "end_time": 1000,
                                "words": [
                                    {"text": "你好", "start_time": 0, "end_time": 400},
                                    {"text": "世界", "start_time": 400, "end_time": 1000},
                                ],
                            }
                        ],
                    }
                },
            )

    provider = DoubaoProvider(
        session=FakeSession(),
        request_id_factory=lambda: "task-1",
        sleep=lambda _seconds: None,
    )

    result = provider.transcribe_units(
        [{"audio_path": str(audio_path), "audio_url": "https://cdn.example.com/voice.wav"}],
        model_name="base",
        language="zh",
        beam_size=2,
        workers=3,
    )

    assert len(calls) == 2
    submit_call = calls[0]
    assert submit_call["headers"]["X-Api-App-Key"] == "app-key"
    assert submit_call["headers"]["X-Api-Access-Key"] == "access-key"
    assert submit_call["headers"]["X-Api-Resource-Id"] == "volc.seedasr.auc"
    assert submit_call["headers"]["X-Api-Request-Id"] == "task-1"
    assert submit_call["json"]["audio"]["url"] == "https://cdn.example.com/voice.wav"
    assert submit_call["json"]["audio"]["format"] == "wav"
    assert submit_call["json"]["audio"]["language"] == "zh-CN"
    assert submit_call["json"]["request"]["model_name"] == "bigmodel"
    assert submit_call["json"]["request"]["show_utterances"] is True
    assert calls[1]["headers"]["X-Tt-Logid"] == "log-1"
    assert result == [
        [
            {"start": 0.0, "end": 0.2, "text": "你"},
            {"start": 0.2, "end": 0.4, "text": "好"},
            {"start": 0.4, "end": 0.7, "text": "世"},
            {"start": 0.7, "end": 1.0, "text": "界"},
        ]
    ]


def test_doubao_transcribe_segments_uses_utterance_timings(monkeypatch, tmp_path: Path):
    from bworkflow_sql.asr.providers.doubao import DoubaoProvider

    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_API_KEY", "api-key")
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_POLL_INTERVAL_SEC", "0")
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_URL_ROOT", "https://cdn.example.com/asr")

    audio_path = tmp_path / "voice.mp3"
    audio_path.write_bytes(b"fake-mp3")
    calls: list[dict[str, Any]] = []

    class FakeSession:
        def post(self, url: str, *, json: Any, headers: dict[str, str], timeout: float):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            if url.endswith("/submit"):
                return FakeResponse(headers={"X-Api-Status-Code": "20000000"})
            return FakeResponse(
                headers={"X-Api-Status-Code": "20000000"},
                payload={
                    "result": {
                        "utterances": [
                            {"text": "第一句", "start_time": 0, "end_time": 1200},
                            {"text": "第二句", "start_time": 1200, "end_time": 2600},
                        ]
                    }
                },
            )

    provider = DoubaoProvider(
        session=FakeSession(),
        request_id_factory=lambda: "task-2",
        sleep=lambda _seconds: None,
    )

    result = provider.transcribe_segments(audio_path, model_name="bigmodel", language="zh", beam_size=2)

    assert result == [
        {"start": 0.0, "end": 1.2, "text": "第一句"},
        {"start": 1.2, "end": 2.6, "text": "第二句"},
    ]
    assert calls[0]["json"]["audio"]["url"] == "https://cdn.example.com/asr/voice.mp3"


def test_doubao_provider_rejects_local_audio_without_public_url(monkeypatch, tmp_path: Path):
    import pytest

    from bworkflow_sql.asr.providers.doubao import DoubaoProvider

    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_API_KEY", "api-key")
    monkeypatch.delenv("BWORKFLOW_DOUBAO_ASR_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BWORKFLOW_DOUBAO_ASR_URL_ROOT", raising=False)

    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wave")
    provider = DoubaoProvider()

    with pytest.raises(ValueError, match="requires an audio URL"):
        provider.transcribe_segments(audio_path, model_name="bigmodel", language="zh", beam_size=2)


def test_doubao_transcribe_segments_accepts_result_list_payload(monkeypatch, tmp_path: Path):
    from bworkflow_sql.asr.providers.doubao import DoubaoProvider

    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_API_KEY", "api-key")
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_POLL_INTERVAL_SEC", "0")

    class FakeSession:
        def post(self, url: str, *, json: Any, headers: dict[str, str], timeout: float):
            if url.endswith("/submit"):
                return FakeResponse(headers={"X-Api-Status-Code": "20000000"})
            return FakeResponse(
                headers={"X-Api-Status-Code": "20000000"},
                payload={
                    "result": [
                        {
                            "utterances": [
                                {"text": "列表结果", "start_time": 100, "end_time": 900},
                            ]
                        }
                    ]
                },
            )

    provider = DoubaoProvider(
        session=FakeSession(),
        request_id_factory=lambda: "task-3",
        sleep=lambda _seconds: None,
    )

    result = provider.transcribe_segments(
        "https://cdn.example.com/voice.wav",
        model_name="bigmodel",
        language="zh",
        beam_size=2,
    )

    assert result == [{"start": 0.1, "end": 0.9, "text": "列表结果"}]
