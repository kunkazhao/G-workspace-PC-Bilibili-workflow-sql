from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class VoiceSynthesisIdentity:
    provider: str
    model: str
    voice_id: str
    settings_hash: str

    def fingerprint(self, *, account_label: str, text_hash: str) -> str:
        return stable_payload_hash(
            {
                "account_label": account_label,
                "model": self.model,
                "provider": self.provider,
                "settings_hash": self.settings_hash,
                "text_hash": text_hash,
                "voice_id": self.voice_id,
            }
        )


@dataclass(frozen=True)
class TtsSynthesisRequest:
    text: str
    identity: VoiceSynthesisIdentity
    output_path: Path
    settings: dict[str, Any]


@dataclass(frozen=True)
class TtsSynthesisResult:
    audio_path: Path
    provider_request_id: str = ""


@dataclass(frozen=True)
class TtsProviderCapabilities:
    output_suffix: str
    requires_local_service: bool = False


class TtsProvider(Protocol):
    name: str
    capabilities: TtsProviderCapabilities
    default_settings: dict[str, Any]

    def prepare(self) -> None:
        ...

    def synthesize(self, request: TtsSynthesisRequest) -> TtsSynthesisResult:
        ...


class TtsProviderRegistry:
    def __init__(self, providers: list[TtsProvider] | tuple[TtsProvider, ...] = ()) -> None:
        self._providers: dict[str, TtsProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: TtsProvider) -> None:
        name = str(provider.name).strip().casefold()
        if not name:
            raise ValueError("TTS provider name cannot be empty")
        if name in self._providers:
            raise ValueError(f"duplicate TTS provider: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> TtsProvider:
        normalized = str(name).strip().casefold()
        provider = self._providers.get(normalized)
        if provider is None:
            available = ", ".join(sorted(self._providers)) or "(none)"
            raise ValueError(f"unknown TTS provider: {name}; available: {available}")
        return provider

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


def stable_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
