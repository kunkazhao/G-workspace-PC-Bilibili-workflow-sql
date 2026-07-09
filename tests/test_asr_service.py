from __future__ import annotations

from pathlib import Path

import pytest


def test_transcribe_jobs_uses_configured_provider(monkeypatch, tmp_path: Path):
    from bworkflow_sql.asr import service
    from bworkflow_sql.asr.providers.base import AsrProvider

    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wave")

    class FakeProvider(AsrProvider):
        name = "fake"

        def transcribe_units(self, jobs, *, model_name, language, beam_size, workers, vad_filter=False):
            assert jobs == [{"audio_path": str(audio_path)}]
            assert model_name == "model-x"
            assert language == "zh"
            assert beam_size == 7
            assert workers == 4
            assert vad_filter is True
            return [[{"start": 0.1, "end": 0.2, "text": "测"}]]

        def transcribe_segments(self, audio_path, *, model_name, language, beam_size, vad_filter=True):
            raise AssertionError("not used")

    monkeypatch.setattr(service, "_PROVIDERS", {"fake": FakeProvider()})
    monkeypatch.setenv("BWORKFLOW_ASR_PROVIDER", "fake")

    result = service.transcribe_jobs(
        [{"audio_path": str(audio_path)}],
        model_name="model-x",
        language="zh",
        beam_size=7,
        workers=4,
        vad_filter=True,
    )

    assert result == [[{"start": 0.1, "end": 0.2, "text": "测"}]]


def test_unknown_provider_lists_available_names(monkeypatch):
    from bworkflow_sql.asr import service

    monkeypatch.setattr(service, "_PROVIDERS", {})

    with pytest.raises(ValueError, match="unknown ASR provider"):
        service.get_provider("missing")


def test_doubao_provider_is_registered_and_selected_by_env(monkeypatch):
    from bworkflow_sql.asr import service

    monkeypatch.setenv("BWORKFLOW_ASR_PROVIDER", "doubao")

    assert service.provider_label() == "doubao"


def test_doubao_provider_aliases_share_one_provider_instance():
    from bworkflow_sql.asr import service

    assert service.get_provider("doubao") is service.get_provider("volcengine-doubao")
