from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from ..utils import safe_text


@dataclass(frozen=True)
class CloudAudioSource:
    url: str
    format: str


def resolve_cloud_audio_source(
    job: dict[str, Any],
    *,
    env_prefix: str,
    provider_label: str,
) -> CloudAudioSource:
    explicit_url = safe_text(job.get("audio_url") or job.get("url"))
    if explicit_url:
        return CloudAudioSource(url=explicit_url, format=audio_format(explicit_url))

    audio_value = safe_text(job.get("audio_path"))
    if looks_like_url(audio_value):
        return CloudAudioSource(url=audio_value, format=audio_format(audio_value))

    single_url = safe_text(os.environ.get(f"{env_prefix}_AUDIO_URL"))
    if single_url:
        return CloudAudioSource(url=single_url, format=audio_format(single_url))

    if not audio_value:
        raise ValueError(f"{provider_label} requires an audio URL, but audio_path is empty.")
    audio_path = Path(audio_value)
    if not audio_path.exists():
        raise ValueError(f"{provider_label} audio file does not exist: {audio_path}")

    local_root = safe_text(os.environ.get(f"{env_prefix}_LOCAL_ROOT"))
    url_root = safe_text(os.environ.get(f"{env_prefix}_URL_ROOT"))
    if local_root and url_root:
        try:
            relative = audio_path.resolve().relative_to(Path(local_root).resolve())
        except ValueError as exc:
            raise ValueError(
                f"{provider_label} local audio is outside {env_prefix}_LOCAL_ROOT: "
                f"audio={audio_path}, local_root={local_root}"
            ) from exc
        relative_url = quote(relative.as_posix(), safe="/")
        return CloudAudioSource(url=f"{url_root.rstrip('/')}/{relative_url}", format=audio_format(audio_path.name))

    raise ValueError(
        f"{provider_label} requires an audio URL. Pass job['audio_url'], pass an HTTP(S) audio_path, "
        f"or configure {env_prefix}_LOCAL_ROOT + {env_prefix}_URL_ROOT to map local files to public URLs."
    )


def looks_like_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def audio_format(value: str) -> str:
    path = urlparse(value).path if looks_like_url(value) else value
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix or "wav"
