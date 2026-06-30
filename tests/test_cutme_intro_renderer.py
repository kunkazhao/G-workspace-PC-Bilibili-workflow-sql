from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import bworkflow_sql.cutme_intro as cutme_intro_module


def test_run_cutme_render_passes_remotion_renderer_by_default(
    tmp_path: Path,
    monkeypatch,
):
    config_path = tmp_path / "cutme-config.json"
    output_path = tmp_path / "intro.mp4"
    config_path.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        output_path.write_bytes(b"mp4")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(cutme_intro_module.subprocess, "run", fake_run)

    result = cutme_intro_module.run_cutme_render(config_path, output_path)

    assert result == output_path
    command = calls[0]
    assert command[:3] == [sys.executable, "-m", "cutme"]
    assert "--renderer" in command
    assert command[command.index("--renderer") + 1] == "remotion"
    assert "--output" in command
    assert str(output_path) in command
    assert "--clean" in command


def test_run_cutme_render_allows_explicit_hyperframes_fallback(
    tmp_path: Path,
    monkeypatch,
):
    config_path = tmp_path / "cutme-config.json"
    output_path = tmp_path / "intro.mp4"
    config_path.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        output_path.write_bytes(b"mp4")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(cutme_intro_module.subprocess, "run", fake_run)

    cutme_intro_module.run_cutme_render(
        config_path,
        output_path,
        renderer="hyperframes",
    )

    command = calls[0]
    assert command[command.index("--renderer") + 1] == "hyperframes"
