from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.workflow_service import WorkflowService


def seed_project(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project(
        {
            "name": "数码-有线耳机",
            "category_id": "cat-1",
            "category_name": "有线耳机",
            "scheme_id": "scheme-1",
            "scheme_name": "模板1",
            "image_root": str(tmp_path / "images"),
            "video_root": str(tmp_path / "videos"),
            "voice_root": str(tmp_path / "voice"),
            "output_root": str(tmp_path / "out"),
        }
    )
    repo.upsert_products_from_master(project_id, [{"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59元"}])
    parsed = parse_markdown_text(
        """
## 引言文案

### 引言1
今天聊有线耳机。

## 商品文案

### 竹林鸟夜莺Z1-YXEJ002-59元
#### 正文
这是商品文案。

## 价格过渡文案

### 0-100
这个价格段值得看。
""".strip()
    )
    SyncService(db).sync_markdown_payload(project_id, parsed)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, voice_id, voice_name, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'voice-1', '小燃音色', 'now', 'now')
            """
        )
    return db, project_id


def test_workflow_commands_use_legacy_script_flags(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)

    voice = service.build_voice_command(project_id, account_label="小燃", uids=["YXEJ002"])
    assert "--markdown-path" in voice
    assert "--registry-path" in voice
    assert "--uids" in voice
    assert "YXEJ002" in voice

    assembly = service.build_assembly_command(project_id, mode="top", top_uids=["YXEJ002"], account_label="小燃", intro_index=1)
    assert "--source-markdown" in assembly
    assert "--output-markdown" in assembly
    assert "--manifest-output" in assembly
    assert "--assembly-mode" in assembly
    assert "top3" in assembly
    assert "--account-id" in assembly
    assert "xiaoran" in assembly
    assert "--markdown-path" not in assembly
    assert "--out-dir" not in assembly

    jianying = service.build_jianying_command(project_id, draft_name="数码/有线耳机")
    assert "--manifest" in jianying
    assert "--draft-name" in jianying
    assert "数码_有线耳机" in jianying
    assert "--draft-root" in jianying
    assert "--output-dir" not in jianying


def test_export_markdown_uses_database_asset_bindings_and_asset_sync_dedupes(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    project = Repository(db).project(project_id)
    image_root = Path(project["image_root"])
    video_root = Path(project["video_root"])
    image_root.mkdir(parents=True)
    video_root.mkdir(parents=True)
    image_path = image_root / "59-YXEJ002-竹林鸟夜莺Z1.png"
    video_path = video_root / "59-YXEJ002-竹林鸟夜莺Z1.mp4"
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")

    sync = SyncService(db)
    first = sync.sync_assets(project_id)
    second = sync.sync_assets(project_id)
    assert first["image"] == 1
    assert second["image"] == 1

    assets = Repository(db).asset_bindings(project_id)
    assert len([asset for asset in assets if asset["asset_type"] == "image"]) == 1
    assert len([asset for asset in assets if asset["asset_type"] == "video"]) == 1

    markdown_path = WorkflowService(db).export_project_markdown(project_id)
    text = markdown_path.read_text(encoding="utf-8")
    assert f"图片：{image_path}" in text
    assert f"视频：{video_path}" in text
