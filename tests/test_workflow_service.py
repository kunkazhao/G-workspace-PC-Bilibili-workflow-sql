from pathlib import Path
import json

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.settings import INTERNAL_WORKSPACE_ROOT
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.utils import now_iso, text_hash
from bworkflow_sql.workflow_service import DEFAULT_CLOSING_TEXT, WorkflowService


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
            "spoken_md_path": str(tmp_path / "口播稿.md"),
            "output_root": str(tmp_path / "legacy-out"),
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


def test_workflow_commands_use_internal_tasks(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)

    voice = service.build_voice_command(project_id, account_label="小燃", uids=["YXEJ002"])
    assert voice[0] == "internal:voice"
    assert "--project-id" in voice
    assert "audio_segment_registry.json" not in " ".join(voice)
    assert "run_peiyindan.py" not in " ".join(voice)
    assert "--uids" in voice
    assert "YXEJ002" in voice

    assembly = service.build_assembly_command(project_id, mode="top", top_uids=["YXEJ002"], account_label="小燃", intro_index=1)
    assert assembly[0] == "internal:assembly"
    assert "--output-markdown" in assembly
    assert "--mode" in assembly
    assert "top" in assembly
    assert "--account-label" in assembly
    assert "小燃" in assembly
    assert "generate_spoken_script.py" not in " ".join(assembly)
    assert "audio_segment_registry.json" not in " ".join(assembly)
    assert str(tmp_path / "口播稿.md") in assembly
    internal_manifest = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / "口播稿.manifest.json"
    assert "--markdown-path" not in assembly
    assert "--out-dir" not in assembly

    intro_video = tmp_path / "intro.mp4"
    intro_video.write_bytes(b"video")
    jianying = service.build_jianying_command(
        project_id,
        draft_name="数码/有线耳机",
        intro_video_path=intro_video,
    )
    assert jianying[0] == "internal:jianying"
    assert "--manifest" in jianying
    assert str(internal_manifest) in jianying
    assert "--intro-video" in jianying
    assert str(intro_video) in jianying
    assert "--draft-name" in jianying
    assert "数码_有线耳机" in jianying
    assert "--draft-root" in jianying
    assert r"E:\剪辑-剪映\草稿\JianyingPro Drafts" in jianying
    assert "--output-dir" not in jianying
    assert "generate_jianying_draft_with_display_videos.py" not in " ".join(jianying)


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
    assert "data" in str(markdown_path)
    assert "workspace" in str(markdown_path)
    text = markdown_path.read_text(encoding="utf-8")
    assert f"图片：{image_path}" in text
    assert f"视频：{video_path}" in text


def test_assembly_generates_spoken_markdown_and_internal_manifest_from_database(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    image_path = Path(project["image_root"]) / "59-YXEJ002-竹林鸟夜莺Z1.png"
    video_path = Path(project["video_root"]) / "59-YXEJ002-竹林鸟夜莺Z1.mp4"
    voice_path = Path(project["voice_root"]) / "小燃-有线耳机" / "01-YXEJ002-竹林鸟夜莺Z1.wav"
    image_path.parent.mkdir(parents=True)
    video_path.parent.mkdir(parents=True)
    voice_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    video_path.write_bytes(b"video")
    voice_path.write_bytes(b"voice")
    product_block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'image', '', '', ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, str(image_path)),
        )
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'video', '', '', ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, str(video_path)),
        )
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', ?, 'voice', '小燃', 'xiaoran', ?, ?, ?, 'ready', 'test', 'now', 'now')
            """,
            (project_id, product_block["id"], product_block["block_label"], product_block["text_hash"], str(voice_path)),
        )

    result = service.run_command(
        service.build_assembly_command(project_id, mode="standard", account_label="小燃", intro_index=1)
    )

    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    assert spoken_path.exists()
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    product_entries = [entry for entry in payload["entries"] if entry["type"] == "product"]
    assert product_entries
    assert product_entries[0]["audio_path"] == str(voice_path)
    assert product_entries[0]["image_path"] == str(image_path)
    assert product_entries[0]["video_path"] == str(video_path)
    intro_entry = next(entry for entry in payload["entries"] if entry["section"] == "intro")
    assert intro_entry["image_path"] == ""
    assert intro_entry["video_path"] == ""


def test_assembly_writes_reader_friendly_spoken_markdown_without_repeated_price_sections(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "Product One", "price_label": "59"},
            {"uid": "YXEJ003", "title": "Product Two", "price_label": "79"},
        ],
    )
    second_body = "SECOND PRODUCT BODY."
    ts = now_iso()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'product', 'YXEJ003', '', '正文', ?, ?, 'test', '', ?, ?)
            """,
            (project_id, second_body, text_hash(second_body), ts, ts),
        )

    result = service.run_command(service.build_assembly_command(project_id, mode="standard", account_label="小燃"))

    assert result.returncode == 0
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    text = spoken_path.read_text(encoding="utf-8")
    price_body = next(block["body"] for block in repo.script_blocks(project_id) if block["script_type"] == "price_transition")
    assert not any(line.startswith("#") for line in text.splitlines())
    assert text.count(price_body) == 1
    assert second_body in text
    assert text.rstrip().endswith(DEFAULT_CLOSING_TEXT)


def test_assembly_matches_imported_voice_by_uid_account_and_hash_without_script_block_id(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    voice_path = Path(project["voice_root"]) / "小燃-有线耳机" / "59-YXEJ002-竹林鸟夜莺Z1.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"voice")
    product_block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, account_id, block_label, text_hash, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'YXEJ002', 'voice', '小燃', 'xiaoran', ?, ?, ?, 'ready', 'legacy_import', 'now', 'now')
            """,
            (project_id, product_block["block_label"], product_block["text_hash"], str(voice_path)),
        )

    result = service.run_command(service.build_assembly_command(project_id, account_label="小燃"))

    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    product_entry = next(entry for entry in payload["entries"] if entry["type"] == "product")
    assert product_entry["audio_path"] == str(voice_path)


def test_jianying_intro_video_filters_intro_manifest_entries(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)

    result = service.run_command(
        service.build_assembly_command(project_id, mode="standard", account_label="小燃", intro_index=1)
    )
    assert result.returncode == 0

    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    intro_video = tmp_path / "intro.mp4"
    intro_video.write_bytes(b"video")

    filtered = service._jianying_manifest_for_intro_video(project_id, manifest_path, intro_video=intro_video)
    payload = json.loads(filtered.read_text(encoding="utf-8"))

    assert payload["intro_video_path"] == str(intro_video)
    assert all(entry.get("section") != "intro" for entry in payload["entries"])
    assert any(entry.get("type") == "product" for entry in payload["entries"])


def test_top_mode_writes_top_products_before_price_transitions_and_adds_closing(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    closing_audio = tmp_path / "closing.wav"
    closing_audio.write_bytes(b"closing")
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "Top One", "price_label": "59"},
            {"uid": "YXEJ006", "title": "Top Two", "price_label": "199"},
            {"uid": "YXEJ007", "title": "Top Three", "price_label": "299"},
            {"uid": "YXEJ008", "title": "Normal Cheap", "price_label": "69"},
            {"uid": "YXEJ009", "title": "Normal Mid", "price_label": "199"},
        ],
    )
    ts = now_iso()
    rows = [
        ("YXEJ006", "TOP TWO BODY."),
        ("YXEJ007", "TOP THREE BODY."),
        ("YXEJ008", "NORMAL CHEAP BODY."),
        ("YXEJ009", "NORMAL MID BODY."),
    ]
    with db.connect() as conn:
        conn.execute("UPDATE accounts SET closing_audio_path=? WHERE label='小燃'", (str(closing_audio),))
        conn.execute("UPDATE script_blocks SET body='TOP ONE BODY.', text_hash=? WHERE project_id=? AND owner_uid='YXEJ002'", (text_hash("TOP ONE BODY."), project_id))
        for uid, body in rows:
            conn.execute(
                """
                INSERT INTO script_blocks
                    (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
                VALUES (?, 'product', ?, '', '正文', ?, ?, 'test', '', ?, ?)
                """,
                (project_id, uid, body, text_hash(body), ts, ts),
            )
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'price_transition', '', '100-300', '正文', '100-300 TRANSITION.', ?, 'test', '', ?, ?)
            """,
            (project_id, text_hash("100-300 TRANSITION."), ts, ts),
        )

    result = service.run_command(
        service.build_assembly_command(
            project_id,
            mode="top",
            top_uids=["YXEJ002", "YXEJ006", "YXEJ007"],
            account_label="小燃",
        )
    )

    assert result.returncode == 0
    project = repo.project(project_id)
    spoken_path = Path(project["spoken_md_path"])
    paragraphs = [paragraph.strip() for paragraph in spoken_path.read_text(encoding="utf-8").split("\n\n") if paragraph.strip()]
    assert paragraphs == [
        "今天聊有线耳机。",
        "TOP ONE BODY.",
        "TOP TWO BODY.",
        "TOP THREE BODY.",
        "这个价格段值得看。",
        "NORMAL CHEAP BODY.",
        "100-300 TRANSITION.",
        "NORMAL MID BODY.",
        DEFAULT_CLOSING_TEXT,
    ]

    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    sections = [entry["section"] for entry in payload["entries"]]
    assert sections == ["intro", "top", "top", "top", "price_transition", "product", "price_transition", "product", "closing"]
    closing_entry = payload["entries"][-1]
    assert closing_entry["type"] == "closing"
    assert closing_entry["text"] == DEFAULT_CLOSING_TEXT
    assert closing_entry["audio_path"] == str(closing_audio)
