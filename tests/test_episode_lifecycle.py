from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from bworkflow_sql.cli import _assert_actionable_pipeline_arg
from bworkflow_sql.episode_lifecycle import EpisodeLifecycleError


def test_cli_pipeline_guard_rejects_superseded_episode_before_dispatch(tmp_path: Path) -> None:
    pipeline = tmp_path / ".pipeline.json"
    pipeline.write_text(
        json.dumps(
            {
                "episode_id": "episode:old",
                "lifecycle": {
                    "status": "superseded",
                    "superseded_by_episode_id": "episode:new",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EpisodeLifecycleError) as error:
        _assert_actionable_pipeline_arg(Namespace(pipeline=str(pipeline)))

    assert error.value.replacement_episode_id == "episode:new"
