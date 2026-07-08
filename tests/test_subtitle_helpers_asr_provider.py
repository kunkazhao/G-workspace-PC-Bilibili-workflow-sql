from __future__ import annotations

from pathlib import Path


def test_subtitle_helper_delegates_asr_jobs_to_service(monkeypatch, tmp_path: Path):
    import bworkflow_sql.subtitle_helpers as subtitle_helpers

    audio_path = tmp_path / "voice.wav"
    audio_path.write_bytes(b"fake-wave")
    captured = {}

    def fake_transcribe_jobs(jobs, *, model_name, language, beam_size, workers, provider_name=None, vad_filter=False):
        captured.update(
            {
                "jobs": jobs,
                "model_name": model_name,
                "language": language,
                "beam_size": beam_size,
                "workers": workers,
                "provider_name": provider_name,
                "vad_filter": vad_filter,
            }
        )
        return [[{"start": 0.0, "end": 0.3, "text": "测"}]]

    monkeypatch.setattr(subtitle_helpers.asr_service, "transcribe_jobs", fake_transcribe_jobs)

    result = subtitle_helpers.run_subtitle_asr_worker(
        [{"audio_path": str(audio_path)}],
        model_name="base",
        language="zh",
        beam_size=2,
        workers=3,
    )

    assert result == [[{"start": 0.0, "end": 0.3, "text": "测"}]]
    assert captured == {
        "jobs": [{"audio_path": str(audio_path)}],
        "model_name": "base",
        "language": "zh",
        "beam_size": 2,
        "workers": 3,
        "provider_name": None,
        "vad_filter": False,
    }
