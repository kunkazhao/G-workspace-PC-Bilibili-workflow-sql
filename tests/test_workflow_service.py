from pathlib import Path
import json
import math
import struct
import wave

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.settings import INTERNAL_WORKSPACE_ROOT
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.utils import now_iso, text_hash
import bworkflow_sql.workflow_service as workflow_service_module
from bworkflow_sql.workflow_service import (
    DEFAULT_CLOSING_TEXT,
    VoiceJob,
    WorkflowService,
    compress_internal_silence,
    prepend_silence,
    unique_path,
)


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


def write_test_wav(path: Path, segments: list[tuple[float, float]], *, frame_rate: int = 16000) -> None:
    samples: list[int] = []
    for duration_sec, amplitude in segments:
        frame_count = int(round(duration_sec * frame_rate))
        for index in range(frame_count):
            value = int(amplitude * 32767 * math.sin(2 * math.pi * 440 * (index / frame_rate)))
            samples.append(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(frame_rate)
        writer.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def test_expected_voice_output_dir_matches_account_and_category(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    project = repo.project(project_id)
    account = repo.accounts()[0]

    output_dir = WorkflowService(db).expected_voice_output_dir(project_id, account_label=account["label"])

    assert output_dir == Path(project["voice_root"]) / project["name"] / account["label"]


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
    script_voice = service.build_voice_command(project_id, account_label="Сȼ", script_ids=["product:YXEJ002:V001"])
    assert "--script-ids" in script_voice
    assert "product:YXEJ002:V001" in script_voice

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


def test_voice_filename_uses_price_uid_title_and_duplicate_suffix(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    project = repo.project(project_id)
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="JP097",
        product_name="京东京造JZ990Pro",
        price_label="229元",
        index=1,
        kind="product",
    )

    assert service._voice_filename(job) == "229-JP097-京东京造JZ990Pro-这是.wav"
    existing = Path(project["voice_root"]) / project["name"] / "小燃" / service._voice_filename(job)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"voice")
    assert unique_path(existing).name == "229-JP097-京东京造JZ990Pro-这是-1.wav"


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
    voice_path = Path(project["voice_root"]) / project["name"] / "小燃" / "01-YXEJ002-竹林鸟夜莺Z1.wav"
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


def test_assembly_prefers_current_category_voice_for_shared_price_transition(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    repo.upsert_products_from_master(project_id, [{"uid": "JP071", "title": "狼蛛F87ProV2超神版", "price_label": "359元"}])
    with db.connect() as conn:
        conn.execute("UPDATE projects SET name='数码-键盘', category_name='键盘' WHERE id=?", (project_id,))
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, script_id, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'price_transition', '', '300-500元', '正文', 'price:300-500:V001', ?, ?, 'test', '', 'now', 'now')
            """,
            (project_id, "300到500元值得重点看。", text_hash("300到500元值得重点看。")),
        )
    price_block = next(
        block
        for block in repo.script_blocks(project_id)
        if block["script_type"] == "price_transition" and block["price_range_label"] == "300-500元"
    )
    wrong_voice = tmp_path / "voice" / "数码-有线耳机" / "小燃" / "0-价格-300-500元.wav"
    right_voice = tmp_path / "voice" / "数码-键盘" / "小燃" / "0-价格-300-500元.wav"
    wrong_voice.parent.mkdir(parents=True)
    right_voice.parent.mkdir(parents=True)
    wrong_voice.write_bytes(b"wrong")
    right_voice.write_bytes(b"right")
    with db.connect() as conn:
        for path in (wrong_voice, right_voice):
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, created_at, updated_at)
                VALUES (?, 'PRICE_TRANSITION', ?, 'voice', '小燃', 'xiaoran', '300-500元', 'price:300-500:V001', ?, ?, 'ready', 'test', 'now', 'now')
                """,
                (project_id, price_block["id"], price_block["text_hash"], str(path)),
            )

    result = service.assemble_spoken_script(project_id, account_label="小燃", product_uids=["JP071"])

    assert result.returncode == 0
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    transition = next(entry for entry in payload["entries"] if entry["price_range_label"] == "300-500元")
    assert transition["audio_path"] == str(right_voice)


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


def test_assembly_randomly_selects_one_product_and_price_version(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    ts = now_iso()
    product_version = "PRODUCT VERSION TWO."
    price_version = "PRICE VERSION TWO."
    monkeypatch.setattr(workflow_service_module.random, "choice", lambda items: items[-1])
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'product', 'YXEJ002', '', '版本2', ?, ?, 'test', '', ?, ?)
            """,
            (project_id, product_version, text_hash(product_version), ts, ts),
        )
        conn.execute(
            """
            INSERT INTO script_blocks
                (project_id, script_type, owner_uid, price_range_label, block_label, body, text_hash, source, source_anchor, created_at, updated_at)
            VALUES (?, 'price_transition', '', '0-100', '版本2', ?, ?, 'test', '', ?, ?)
            """,
            (project_id, price_version, text_hash(price_version), ts, ts),
        )

    result = service.run_command(service.build_assembly_command(project_id, mode="standard", account_label="小燃"))

    assert result.returncode == 0
    spoken_path = Path(repo.project(project_id)["spoken_md_path"])
    text = spoken_path.read_text(encoding="utf-8")
    assert price_version in text
    assert product_version in text
    assert "这个价格段值得看。" not in text
    assert "这是商品文案。" not in text
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [entry["source_label"] for entry in payload["entries"][:3]] == ["引言1", "价格过渡 0-100", "版本2"]


def test_assembly_matches_imported_voice_by_uid_account_and_hash_without_script_block_id(tmp_path: Path):
    db, project_id = seed_project(tmp_path)
    repo = Repository(db)
    service = WorkflowService(db)
    project = repo.project(project_id)
    voice_path = Path(project["voice_root"]) / project["name"] / "小燃" / "59-YXEJ002-竹林鸟夜莺Z1.wav"
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


def test_jianying_generation_skips_subtitles_by_default(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    project = Repository(db).project(project_id)
    result = service.run_command(service.build_assembly_command(project_id, account_label="小燃"))
    assert result.returncode == 0
    spoken_path = Path(project["spoken_md_path"])
    manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "manifests" / f"{spoken_path.stem}.manifest.json"
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str]):
        captured["cmd"] = cmd
        return workflow_service_module.WorkflowRunResult(cmd, returncode=0, stdout="ok\n")

    monkeypatch.setattr(workflow_service_module, "run_subprocess_text", fake_run)

    draft = service.generate_jianying_draft(
        project_id,
        manifest_path=manifest_path,
        draft_name="test-draft",
        draft_root=tmp_path / "drafts",
    )

    assert draft.returncode == 0
    assert "--skip-subtitles" in captured["cmd"]


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


def test_compress_internal_silence_shortens_only_internal_long_pauses(tmp_path: Path):
    audio_path = tmp_path / "silence.wav"
    write_test_wav(
        audio_path,
        [
            (0.2, 0.6),
            (0.5, 0.0),
            (0.2, 0.6),
        ],
    )

    result = compress_internal_silence(audio_path)

    assert result["enabled"] is True
    assert result["changed"] is True
    assert result["compressed_count"] == 1
    assert result["original_ms"] == 900
    assert result["fixed_ms"] == 520
    assert result["removed_ms"] == 380


def test_prepend_silence_adds_100ms_to_wav_start(tmp_path: Path):
    audio_path = tmp_path / "voice.wav"
    write_test_wav(audio_path, [(0.2, 0.6)], frame_rate=1000)

    result = prepend_silence(audio_path)

    assert result["changed"] is True
    assert result["silence_ms"] == 100
    with wave.open(str(audio_path), "rb") as reader:
        assert reader.getnframes() == 300
        first_frames = reader.readframes(100)
        next_frames = reader.readframes(2)
    assert first_frames == b"\x00" * 100 * 2
    assert next_frames != b"\x00" * 2 * 2


def test_generate_one_voice_runs_new_project_audio_postprocess(tmp_path: Path, monkeypatch):
    db, project_id = seed_project(tmp_path)
    service = WorkflowService(db)
    repo = Repository(db)
    block = next(block for block in repo.script_blocks(project_id) if block["script_type"] == "product")
    job = VoiceJob(
        block=block,
        uid="YXEJ002",
        product_name="竹林鸟夜莺Z1",
        price_label="59元",
        kind="product",
    )

    generated_path = tmp_path / "generated.wav"
    write_test_wav(generated_path, [(0.2, 0.6), (0.5, 0.0), (0.2, 0.6)])

    class FakeHttp:
        def post(self, url: str, json_payload: dict[str, object]) -> dict[str, str]:
            return {"audio_path": str(generated_path)}

    called: dict[str, Path] = {}

    def fake_compress(path: Path, **_: object) -> dict[str, object]:
        called["path"] = path
        return {"enabled": True, "changed": True}

    monkeypatch.setattr(workflow_service_module, "compress_internal_silence", fake_compress)

    output_path = service._generate_one_voice(
        FakeHttp(),
        job=job,
        account={"label": "小燃"},
        voice_id="voice-1",
        output_dir=tmp_path / "voice-out",
    )

    assert output_path.exists()
    assert called["path"] == output_path
    with wave.open(str(output_path), "rb") as reader:
        assert round(reader.getnframes() * 1000 / reader.getframerate()) == 1000
