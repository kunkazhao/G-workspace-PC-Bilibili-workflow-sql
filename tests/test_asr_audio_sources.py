from __future__ import annotations

from pathlib import Path

import pytest


def test_resolve_cloud_audio_source_uses_explicit_audio_url():
    from bworkflow_sql.asr.audio_sources import resolve_cloud_audio_source

    result = resolve_cloud_audio_source(
        {"audio_path": r"G:\local\voice.wav", "audio_url": "https://cdn.example.com/voice.wav"},
        env_prefix="BWORKFLOW_DOUBAO_ASR",
        provider_label="Doubao ASR",
    )

    assert result.url == "https://cdn.example.com/voice.wav"
    assert result.format == "wav"


def test_resolve_cloud_audio_source_maps_local_path_to_url(monkeypatch, tmp_path: Path):
    from bworkflow_sql.asr.audio_sources import resolve_cloud_audio_source

    nested_dir = tmp_path / "batch 1"
    nested_dir.mkdir()
    audio_path = nested_dir / "voice 01.mp3"
    audio_path.write_bytes(b"fake")
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("BWORKFLOW_DOUBAO_ASR_URL_ROOT", "https://cdn.example.com/asr")

    result = resolve_cloud_audio_source(
        {"audio_path": str(audio_path)},
        env_prefix="BWORKFLOW_DOUBAO_ASR",
        provider_label="Doubao ASR",
    )

    assert result.url == "https://cdn.example.com/asr/batch%201/voice%2001.mp3"
    assert result.format == "mp3"


def test_resolve_cloud_audio_source_rejects_local_path_without_url(monkeypatch, tmp_path: Path):
    from bworkflow_sql.asr.audio_sources import resolve_cloud_audio_source

    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake")
    monkeypatch.delenv("BWORKFLOW_DOUBAO_ASR_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BWORKFLOW_DOUBAO_ASR_URL_ROOT", raising=False)

    with pytest.raises(ValueError, match="requires an audio URL"):
        resolve_cloud_audio_source(
            {"audio_path": str(audio_path)},
            env_prefix="BWORKFLOW_DOUBAO_ASR",
            provider_label="Doubao ASR",
        )
