from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class AsrProvider(ABC):
    name: str

    @abstractmethod
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
        """Return character/word timing units for each job."""

    @abstractmethod
    def transcribe_segments(
        self,
        audio_path: str | Path,
        *,
        model_name: str,
        language: str,
        beam_size: int,
        vad_filter: bool = True,
    ) -> list[dict[str, Any]]:
        """Return ASR text segments with start/end/text fields."""
