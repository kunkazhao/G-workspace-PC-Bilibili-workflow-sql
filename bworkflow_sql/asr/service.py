from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..utils import safe_text
from .providers.base import AsrProvider
from .providers.faster_whisper import FasterWhisperProvider

DEFAULT_ASR_PROVIDER = "faster_whisper"

_PROVIDERS: dict[str, AsrProvider] = {
    FasterWhisperProvider.name: FasterWhisperProvider(),
    "faster-whisper": FasterWhisperProvider(),
    "whisper": FasterWhisperProvider(),
}


def configured_provider_name(provider_name: str | None = None) -> str:
    explicit = safe_text(provider_name)
    if explicit:
        return explicit
    return safe_text(os.environ.get("BWORKFLOW_ASR_PROVIDER")) or DEFAULT_ASR_PROVIDER


def get_provider(provider_name: str | None = None) -> AsrProvider:
    name = configured_provider_name(provider_name)
    provider = _PROVIDERS.get(name)
    if provider is None:
        available = ", ".join(sorted(_PROVIDERS)) or "(none)"
        raise ValueError(f"unknown ASR provider: {name}; available: {available}")
    return provider


def provider_label(provider_name: str | None = None) -> str:
    return get_provider(provider_name).name


def transcribe_jobs(
    jobs: list[dict[str, Any]],
    *,
    model_name: str,
    language: str,
    beam_size: int,
    workers: int,
    provider_name: str | None = None,
    vad_filter: bool = False,
) -> list[list[dict[str, Any]]]:
    provider = get_provider(provider_name)
    return provider.transcribe_units(
        jobs,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        workers=workers,
        vad_filter=vad_filter,
    )


def transcribe_segments(
    audio_path: str | Path,
    *,
    model_name: str,
    language: str,
    beam_size: int,
    provider_name: str | None = None,
    vad_filter: bool = True,
) -> list[dict[str, Any]]:
    provider = get_provider(provider_name)
    return provider.transcribe_segments(
        audio_path,
        model_name=model_name,
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
