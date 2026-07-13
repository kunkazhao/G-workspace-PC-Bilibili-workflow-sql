from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .tts_contracts import (
    TtsProviderCapabilities,
    TtsSynthesisRequest,
    TtsSynthesisResult,
)
from .tts_helpers import DEFAULT_TTS_FIELDS, MINIMAX_DEFAULT_SYNTHESIS_SETTINGS


class IndexTtsProvider:
    name = "indextts"
    capabilities = TtsProviderCapabilities(output_suffix=".wav", requires_local_service=True)
    default_settings = DEFAULT_TTS_FIELDS

    def __init__(
        self,
        *,
        http: Any,
        endpoint: str,
        prepare_callback: Callable[[], None],
        finalize_callback: Callable[[Path, Path], Path],
    ) -> None:
        self._http = http
        self._endpoint = endpoint
        self._prepare_callback = prepare_callback
        self._finalize_callback = finalize_callback

    def prepare(self) -> None:
        self._prepare_callback()

    def synthesize(self, request: TtsSynthesisRequest) -> TtsSynthesisResult:
        payload = {
            "voice_id": request.identity.voice_id,
            "text": request.text,
            "output_name": request.output_path.name,
            **request.settings,
        }
        result = self._http.post(self._endpoint, json_payload=payload)
        if not isinstance(result, dict):
            raise ValueError(f"配音接口返回异常：{result}")
        generated_path = Path(str(result.get("audio_path") or ""))
        if not generated_path.exists():
            raise ValueError(f"配音接口返回成功，但没有找到音频文件：{generated_path}")
        output_path = self._finalize_callback(generated_path, request.output_path)
        return TtsSynthesisResult(audio_path=output_path)


class MiniMaxTtsProvider:
    name = "minimax"
    capabilities = TtsProviderCapabilities(output_suffix=".mp3")
    default_settings = MINIMAX_DEFAULT_SYNTHESIS_SETTINGS

    def __init__(
        self,
        *,
        prepare_callback: Callable[[], None],
        synthesize_callback: Callable[[TtsSynthesisRequest], Path],
    ) -> None:
        self._prepare_callback = prepare_callback
        self._synthesize_callback = synthesize_callback

    def prepare(self) -> None:
        self._prepare_callback()

    def synthesize(self, request: TtsSynthesisRequest) -> TtsSynthesisResult:
        output_path = self._synthesize_callback(request)
        return TtsSynthesisResult(audio_path=output_path)
