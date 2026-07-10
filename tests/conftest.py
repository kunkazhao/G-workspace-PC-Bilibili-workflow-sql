from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_final_video_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from bworkflow_sql import final_video_pipeline

    workspace_root = tmp_path / "bworkflow-workspace"
    monkeypatch.setattr(final_video_pipeline, "INTERNAL_WORKSPACE_ROOT", workspace_root)
    return workspace_root
