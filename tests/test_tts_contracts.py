from pathlib import Path

import pytest

from bworkflow_sql.tts_contracts import (
    TtsProviderCapabilities,
    TtsProviderRegistry,
    TtsSynthesisResult,
)
from bworkflow_sql.tts_helpers import normalize_voice_provider, voice_synthesis_identity


class StubProvider:
    name = "stub"
    capabilities = TtsProviderCapabilities(output_suffix=".wav")
    default_settings = {}

    def prepare(self) -> None:
        pass

    def synthesize(self, request) -> TtsSynthesisResult:
        return TtsSynthesisResult(audio_path=Path(request.output_path))


def test_provider_registry_rejects_unknown_and_duplicate_names():
    registry = TtsProviderRegistry([StubProvider()])

    assert registry.get("STUB").name == "stub"
    with pytest.raises(ValueError, match="unknown TTS provider"):
        registry.get("missing")
    with pytest.raises(ValueError, match="duplicate TTS provider"):
        registry.register(StubProvider())


def test_provider_normalization_fails_closed():
    with pytest.raises(ValueError, match="unknown TTS provider"):
        normalize_voice_provider("typo-provider")


def test_voice_fingerprint_changes_with_provider_or_voice():
    local = voice_synthesis_identity("indextts", "voice-a")
    another_voice = voice_synthesis_identity("indextts", "voice-b")
    cloud = voice_synthesis_identity("minimax", "voice-a")

    fingerprints = {
        identity.fingerprint(account_label="account", text_hash="text-hash")
        for identity in (local, another_voice, cloud)
    }
    assert len(fingerprints) == 3
