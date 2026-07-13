from pathlib import Path

from bworkflow_sql.media_workspace import build_media_workspace_plan, ensure_media_workspace


def test_plan_and_ensure_build_complete_external_workspace(tmp_path: Path):
    project = {"name": "家居-冲牙器", "image_root": str(tmp_path / "images"), "voice_root": str(tmp_path / "voices"), "video_root": str(tmp_path / "roll-b")}
    accounts = [{"label": "小博", "enabled": 1}, {"label": "停用", "enabled": 0}]
    intro_root = tmp_path / "intro"

    plan = build_media_workspace_plan(project, accounts, intro_root=intro_root)
    result = ensure_media_workspace(plan)

    assert result["workspace_ready"] is True
    assert (tmp_path / "roll-b" / "家居-冲牙器").is_dir()
    assert (intro_root / "家居-冲牙器").is_dir()
    assert (tmp_path / "voices" / "家居-冲牙器" / "小博").is_dir()
    assert any(entry["kind"] == "product_images" and entry["account"] == "小博" for entry in plan)
    assert not any(entry["account"] == "停用" for entry in plan)
    assert ensure_media_workspace(plan)["created"] == []
