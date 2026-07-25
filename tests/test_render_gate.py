from __future__ import annotations

from pathlib import Path
from multiprocessing import get_context

import pytest

from bworkflow_sql.render_gate import (
    RenderBusyError,
    acquire_production_render_slot,
)


def _hold_render_gate(lock_root: str, ready: object, release: object) -> None:
    with acquire_production_render_slot(
        {
            "episode_id": "episode:external-process",
            "category": "数码-键盘",
            "account": "小博",
            "phase": "final_video",
        },
        lock_root=lock_root,
    ):
        ready.set()
        release.wait(10)


def test_render_gate_rejects_second_render_and_reports_current_owner(tmp_path: Path) -> None:
    owner = {
        "episode_id": "episode:oven-a",
        "category": "家居-烤箱",
        "account": "小燃",
        "phase": "final_video",
    }

    with acquire_production_render_slot(owner, lock_root=tmp_path):
        with pytest.raises(RenderBusyError) as caught:
            with acquire_production_render_slot(
                {
                    "episode_id": "episode:fan-a",
                    "category": "家居-风扇",
                    "account": "小燃",
                    "phase": "intro_video",
                },
                lock_root=tmp_path,
            ):
                raise AssertionError("busy render must not enter the protected block")

        assert caught.value.code == "render_busy"
        assert caught.value.owner["episode_id"] == "episode:oven-a"
        assert "家居-烤箱 / 小燃 / episode:oven-a" in str(caught.value)
        assert "继续" in str(caught.value)

    assert not (tmp_path / ".locks" / "production-render-owner.json").exists()


def test_render_gate_is_shared_across_processes(tmp_path: Path) -> None:
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_render_gate,
        args=(str(tmp_path), ready, release),
    )
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(RenderBusyError) as caught:
            with acquire_production_render_slot(
                {
                    "episode_id": "episode:local-process",
                    "category": "家居-烤箱",
                    "account": "小燃",
                    "phase": "intro_video",
                },
                lock_root=tmp_path,
            ):
                raise AssertionError("cross-process busy render must be rejected")
        assert caught.value.owner["episode_id"] == "episode:external-process"
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0


def test_intro_render_does_not_start_cutme_while_gate_is_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bworkflow_sql.cutme_intro as intro

    monkeypatch.setattr(intro, "INTERNAL_WORKSPACE_ROOT", tmp_path)
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(intro.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    with acquire_production_render_slot(
        {
            "episode_id": "episode:active",
            "category": "数码-键盘",
            "account": "小博",
            "phase": "final_video",
        },
        lock_root=tmp_path,
    ):
        with pytest.raises(RenderBusyError):
            intro.run_cutme_render(
                config,
                tmp_path / "intro.mp4",
                render_owner={
                    "episode_id": "episode:waiting",
                    "category": "家居-烤箱",
                    "account": "小歪",
                    "phase": "intro_video",
                },
            )

    assert calls == []
