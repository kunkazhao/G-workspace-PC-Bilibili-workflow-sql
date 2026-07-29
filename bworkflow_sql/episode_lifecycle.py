from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EpisodeLifecycleError(ValueError):
    code = "episode_superseded"

    def __init__(self, episode_id: str, replacement_episode_id: str) -> None:
        self.episode_id = episode_id
        self.replacement_episode_id = replacement_episode_id
        super().__init__(
            f"episode {episode_id} 已被 {replacement_episode_id} 替代，B-Workflow 拒绝继续执行该任务"
        )


def assert_pipeline_actionable_payload(payload: dict[str, Any]) -> None:
    lifecycle = payload.get("lifecycle")
    if not isinstance(lifecycle, dict) or str(lifecycle.get("status") or "").strip().lower() != "superseded":
        return
    raise EpisodeLifecycleError(
        str(payload.get("episode_id") or "unknown"),
        str(lifecycle.get("superseded_by_episode_id") or "a newer episode"),
    )


def assert_pipeline_actionable(pipeline_path: str | Path) -> None:
    pipeline = Path(pipeline_path).expanduser().resolve()
    payload: Any = json.loads(pipeline.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("pipeline must contain a JSON object")
    assert_pipeline_actionable_payload(payload)
