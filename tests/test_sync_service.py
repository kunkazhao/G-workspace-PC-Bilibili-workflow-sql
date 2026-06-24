from pathlib import Path

from bworkflow_sql.db import Database
from bworkflow_sql.md_parser import parse_markdown_text
from bworkflow_sql.repositories import Repository
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.utils import text_hash


def test_markdown_sync_only_imports_master_products(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project({"name": "test"})
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59元"},
            {"uid": "YXEJ003", "title": "KZ Gale疾风", "price_label": "79元"},
        ],
    )
    parsed = parse_markdown_text(
        """
## 商品文案

### 竹林鸟夜莺Z1-YXEJ002-59元
正文 A

### 多余商品-YXEJ999-999元
不应该导入
""".strip()
    )
    result = SyncService(db).sync_markdown_payload(project_id, parsed)
    blocks = repo.script_blocks(project_id)
    assert result["upserted"] == 1
    assert len(result["extra_md"]) == 1
    assert len(result["missing_copy"]) == 1
    assert blocks[0]["owner_uid"] == "YXEJ002"


def test_script_hash_matches_legacy_voice_registry_format(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project({"name": "hash-test"})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59元"}],
    )
    parsed = parse_markdown_text(
        """
## 商品文案

### 59元-YXEJ002-竹林鸟夜莺Z1
#### 正文
正常配音文案
""".strip()
    )

    SyncService(db).sync_markdown_payload(project_id, parsed)
    row = db.fetchone("SELECT text_hash FROM script_blocks WHERE project_id=? AND owner_uid=?", (project_id, "YXEJ002"))

    assert row["text_hash"] == text_hash("正常配音文案")
    assert len(row["text_hash"]) == 40


def test_voice_asset_sync_refreshes_markdown_and_preserves_existing_voice_hash(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    md_path = tmp_path / "product.md"
    voice_root = tmp_path / "voice"
    project_id = db.upsert_project({"name": "数码-键盘", "md_path": str(md_path), "voice_root": str(voice_root)})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "YXEJ002", "title": "竹林鸟夜莺Z1", "price_label": "59元"}],
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'now', 'now')
            """
        )
    md_path.write_text(
        """
## 商品文案

### 59元-YXEJ002-竹林鸟夜莺Z1
#### 正文
旧版配音文案
""".strip(),
        encoding="utf-8",
    )
    SyncService(db).sync_markdown(project_id)
    voice_path = voice_root / "数码-键盘" / "小燃" / "59-YXEJ002-竹林鸟夜莺Z1-正文.wav"
    voice_path.parent.mkdir(parents=True)
    voice_path.write_bytes(b"voice")
    SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)
    md_path.write_text(
        """
## 商品文案

### 59元-YXEJ002-竹林鸟夜莺Z1
#### 正文
新版配音文案
""".strip(),
        encoding="utf-8",
    )

    result = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)

    block = db.fetchone("SELECT body, text_hash FROM script_blocks WHERE project_id=? AND owner_uid='YXEJ002'", (project_id,))
    asset = db.fetchone("SELECT text_hash FROM asset_bindings WHERE project_id=? AND asset_type='voice'", (project_id,))
    assert result["voice"] == 1
    assert block["body"] == "新版配音文案"
    assert block["text_hash"] == text_hash("新版配音文案")
    assert asset["text_hash"] == text_hash("旧版配音文案")


def test_markdown_sync_preserves_and_generates_script_ids(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    project_id = db.upsert_project({"name": "script-id-test"})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "JP071", "title": "狼蛛F87ProV2超神版", "price_label": "359元"}],
    )
    parsed = parse_markdown_text(
        """
## 引言文案

<!-- script_id: intro:I009 -->
### 引言9
这是引言。

## 商品文案

### 359元-JP071-狼蛛F87ProV2超神版
<!-- script_id: product:JP071:V009 -->
#### 正文9
这是商品文案。
#### 正文10
这是第二版商品文案。

## 价格过渡文案

### 200元以下
<!-- script_id: price:200-under:V009 -->
#### 正文9
这是价格过渡。
""".strip()
    )

    SyncService(db).sync_markdown_payload(project_id, parsed)
    rows = repo.script_blocks(project_id)

    assert any(row["block_label"] == "引言9" and row["script_id"] == "intro:I009" for row in rows)
    assert any(row["owner_uid"] == "JP071" and row["block_label"] == "正文9" and row["script_id"] == "product:JP071:V009" for row in rows)
    assert any(row["owner_uid"] == "JP071" and row["block_label"] == "正文10" and row["script_id"] == "product:JP071:V002" for row in rows)
    assert any(row["price_range_label"] == "200元以下" and row["script_id"] == "price:200-under:V009" for row in rows)


def test_sync_markdown_writes_missing_script_id_comments(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    md_path = tmp_path / "copy.md"
    md_path.write_text(
        """
## 商品文案

### 359元-JP071-狼蛛F87ProV2超神版
#### 正文
这是商品文案。
""".strip(),
        encoding="utf-8",
    )
    project_id = db.upsert_project({"name": "script-id-writeback", "md_path": str(md_path)})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "JP071", "title": "狼蛛F87ProV2超神版", "price_label": "359元"}],
    )

    SyncService(db).sync_markdown(project_id)

    assert "<!-- script_id: product:JP071:V001 -->" in md_path.read_text(encoding="utf-8")


def test_master_sync_forces_fresh_scheme_summary(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test.db")
    project_id = db.upsert_project(
        {
            "name": "keyboard",
            "workspace_id": "workspace-1",
            "scheme_id": "scheme-1",
        }
    )
    calls = []

    def fake_fetch_summary(self, *, workspace_id, scheme_id, force_refresh=False):
        calls.append(
            {
                "workspace_id": workspace_id,
                "scheme_id": scheme_id,
                "force_refresh": force_refresh,
            }
        )
        return {"items": [{"uid": "JP096", "title": "狼蛛F75Max 客制化", "price": "279元"}]}

    monkeypatch.setattr("bworkflow_sql.master_data.MasterDataService.fetch_scheme_summary", fake_fetch_summary)

    result = SyncService(db).sync_master_scheme(project_id, apply_changes=False)

    assert calls == [{"workspace_id": "workspace-1", "scheme_id": "scheme-1", "force_refresh": True}]
    assert result["added"] == [{"uid": "JP096", "title": "狼蛛F75Max 客制化", "price_label": "279元", "master_item_id": "", "sort_order": 1}]


def test_master_sync_trusts_matching_category_id_when_names_are_aliases(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "test.db")
    project_id = db.upsert_project(
        {
            "name": "数码-入耳蓝牙耳机",
            "workspace_id": "workspace-1",
            "category_id": "category-1",
            "category_name": "入耳蓝牙耳机",
            "scheme_id": "scheme-1",
            "scheme_name": "赵二-b站-入耳式",
            "md_path": r"G:\WriteSpace\B站-文案脚本\10_b站文案\3.商品文案\数码-入耳蓝牙耳机.md",
        }
    )

    def fake_fetch_summary(self, *, workspace_id, scheme_id, force_refresh=False):
        return {
            "category_id": "category-1",
            "category_name": "耳机-入耳",
            "items": [{"uid": "EJ001", "title": "示例耳机", "price": "99元"}],
        }

    monkeypatch.setattr("bworkflow_sql.master_data.MasterDataService.fetch_scheme_summary", fake_fetch_summary)

    result = SyncService(db).sync_master_scheme(project_id, apply_changes=False)

    assert result["added"] == [{"uid": "EJ001", "title": "示例耳机", "price_label": "99元", "master_item_id": "", "sort_order": 1}]


def test_asset_sync_ignores_extra_files_and_reports_missing_scheme_products(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    image_root = tmp_path / "images"
    image_root.mkdir()
    matched = image_root / "1-JP096-狼蛛F75Max.png"
    extra = image_root / "2-JP999-额外商品.png"
    matched.write_bytes(b"image")
    extra.write_bytes(b"image")
    project_id = db.upsert_project({"name": "keyboard", "image_root": str(image_root)})
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "JP096", "title": "狼蛛F75Max", "price_label": "279元"},
            {"uid": "JP097", "title": "京东京造JZ990Pro", "price_label": "399元"},
        ],
    )

    result = SyncService(db).sync_assets(project_id, asset_type="image", root_override=image_root)

    assert result["image"] == 1
    assert result["unmatched"] == 1
    assert result["scanned_roots"] == {"image": str(image_root)}
    assert result["matched_items"][0]["uid"] == "JP096"
    assert result["matched_items"][0]["title"] == "狼蛛F75Max"
    assert result["matched_items"][0]["path"] == str(matched)
    assert result["unmatched_items"][0]["uid"] == "JP097"
    assert result["unmatched_items"][0]["title"] == "京东京造JZ990Pro"
    assert result["unmatched_items"][0]["asset_type"] == "image"


def test_image_asset_sync_reports_missing_when_selected_template_folder_has_no_image(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    image_root = tmp_path / "images"
    template1 = image_root / "数码-键盘" / "小燃" / "模板1"
    template2 = image_root / "数码-键盘" / "小燃" / "模板2"
    template1.mkdir(parents=True)
    template2.mkdir(parents=True)
    (template1 / "1-JP096-狼蛛F75Max.png").write_bytes(b"template1")
    project_id = db.upsert_project({"name": "数码-键盘", "category_name": "键盘", "image_root": str(image_root)})
    repo.upsert_products_from_master(project_id, [{"uid": "JP096", "title": "狼蛛F75Max", "price_label": "279元"}])
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'now', 'now')
            """
        )

    result = SyncService(db).sync_assets(project_id, asset_type="image", root_override=template2)

    assert result["image"] == 0
    assert result["unmatched"] == 1
    assert result["unmatched_items"][0]["uid"] == "JP096"
    assert result["scanned_roots"] == {"image": str(template2)}


def test_video_asset_sync_matches_overlapping_uid_tokens(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    video_root = tmp_path / "videos"
    video_root.mkdir()
    ly_video = video_root / "149元-LY018-瓷音未来Mars 2i.mp4"
    rely_video = video_root / "186元-RELY018-西圣 A1.mp4"
    ly_video.write_bytes(b"video")
    rely_video.write_bytes(b"video")
    project_id = db.upsert_project({"name": "earbuds", "video_root": str(video_root)})
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "LY018", "title": "瓷音未来Mars 2i", "price_label": "149元"},
            {"uid": "RELY018", "title": "西圣 A1", "price_label": "186元"},
        ],
    )

    result = SyncService(db).sync_assets(project_id, asset_type="video", root_override=video_root)

    assert result["video"] == 2
    assert result["unmatched"] == 0
    assert {item["uid"] for item in result["matched_items"]} == {"LY018", "RELY018"}
    assert db.fetchone(
        "SELECT uid FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(rely_video)),
    )["uid"] == "RELY018"


def test_video_asset_sync_rejects_substring_uid_without_token_boundaries(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    video_root = tmp_path / "videos"
    video_root.mkdir()
    typo_video = video_root / "499元-RELY028-水月雨梦回2.mp4"
    typo_video.write_bytes(b"video")
    project_id = db.upsert_project({"name": "earbuds", "video_root": str(video_root)})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "LY028", "title": "水月雨梦回2", "price_label": "499元"}],
    )

    result = SyncService(db).sync_assets(project_id, asset_type="video", root_override=video_root)

    assert result["video"] == 0
    assert result["unmatched"] == 1
    assert result["matched_items"] == []
    assert result["unmatched_items"][0]["uid"] == "LY028"


def test_video_asset_sync_keeps_rematched_path_ready(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    video_root = tmp_path / "videos"
    video_root.mkdir()
    rely_video = video_root / "186元-RELY018-西圣 A1.mp4"
    rely_video.write_bytes(b"video")
    project_id = db.upsert_project({"name": "earbuds", "video_root": str(video_root)})
    repo.upsert_products_from_master(
        project_id,
        [
            {"uid": "LY018", "title": "瓷音未来Mars 2i", "price_label": "149元"},
            {"uid": "RELY018", "title": "西圣 A1", "price_label": "186元"},
        ],
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'LY018', 'video', '', ?, 'ready', 'scan', 'now', 'now')
            """,
            (project_id, str(rely_video)),
        )

    result = SyncService(db).sync_assets(project_id, asset_type="video", root_override=video_root)

    assert result["video"] == 1
    assert result["unmatched_items"][0]["uid"] == "LY018"
    rows = db.fetchall(
        "SELECT uid, status FROM asset_bindings WHERE project_id=? AND path=? ORDER BY uid",
        (project_id, str(rely_video)),
    )
    assert [(row["uid"], row["status"]) for row in rows] == [("LY018", "stale"), ("RELY018", "ready")]


def test_asset_sync_reports_added_removed_and_current_items(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    image_root = tmp_path / "images"
    image_root.mkdir()
    matched = image_root / "1-AB001-Keyboard.png"
    matched.write_bytes(b"image")
    project_id = db.upsert_project({"name": "keyboard", "image_root": str(image_root)})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "AB001", "title": "Keyboard", "price_label": "199"}],
    )

    first = SyncService(db).sync_assets(project_id, asset_type="image", root_override=image_root)
    second = SyncService(db).sync_assets(project_id, asset_type="image", root_override=image_root)
    matched.unlink()
    third = SyncService(db).sync_assets(project_id, asset_type="image", root_override=image_root)

    assert [item["uid"] for item in first["added_items"]] == ["AB001"]
    assert first["removed_items"] == []
    assert [item["uid"] for item in first["current_items"]] == ["AB001"]
    assert second["added_items"] == []
    assert second["removed_items"] == []
    assert [item["uid"] for item in second["current_items"]] == ["AB001"]
    assert third["added_items"] == []
    assert [item["uid"] for item in third["removed_items"]] == ["AB001"]
    assert third["current_items"] == []
    assert repo.asset_bindings(project_id)[0]["status"] == "stale"


def test_video_sync_stales_missing_legacy_binding_outside_current_root(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    old_root = tmp_path / "old-videos"
    current_root = tmp_path / "current-videos"
    old_root.mkdir()
    current_root.mkdir()
    current_video = current_root / "199-AB001-Keyboard.mp4"
    current_video.write_bytes(b"current")
    missing_old_video = old_root / "old-AB001-Keyboard.mp4"
    project_id = db.upsert_project({"name": "keyboard", "video_root": str(current_root)})
    repo.upsert_products_from_master(
        project_id,
        [{"uid": "AB001", "title": "Keyboard", "price_label": "199"}],
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, asset_type, account_label, path, status, source_kind, created_at, updated_at)
            VALUES (?, 'AB001', 'video', '', ?, 'ready', 'legacy_folder_scan', 'now', 'now')
            """,
            (project_id, str(missing_old_video)),
        )

    result = SyncService(db).sync_assets(project_id, asset_type="video", root_override=current_root)

    assert result["video"] == 1
    assert [item["path"] for item in result["current_items"]] == [str(current_video)]
    assert [item["path"] for item in result["removed_items"]] == [str(missing_old_video)]
    rows = db.fetchall(
        "SELECT path, status FROM asset_bindings WHERE project_id=? AND asset_type='video' ORDER BY path",
        (project_id,),
    )
    assert {row["path"]: row["status"] for row in rows} == {
        str(current_video): "ready",
        str(missing_old_video): "stale",
    }


def test_voice_asset_sync_includes_intro_and_price_transition_blocks(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    voice_root = tmp_path / "voice"
    voice_root.mkdir()
    intro = voice_root / "0-引言-引言2-今天.wav"
    price = voice_root / "0-价格-200元以下-这个.wav"
    intro.write_bytes(b"voice")
    price.write_bytes(b"voice")
    project_id = db.upsert_project({"name": "keyboard", "voice_root": str(voice_root)})
    parsed = parse_markdown_text(
        """
## 引言文案

### 引言1
第一段

### 引言2
第二段

## 价格过渡文案

### 200元以下
这个价位
""".strip()
    )
    SyncService(db).sync_markdown_payload(project_id, parsed)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'now', 'now')
            """
        )

    result = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)

    assert result["voice"] == 2
    assert result["unmatched"] == 1
    assert {item["uid"] for item in result["matched_items"]} == {"INTRO", "PRICE_TRANSITION"}
    assert result["unmatched_items"][0]["title"] == "引言 引言1"
    assets = repo.asset_bindings(project_id)
    assert any(asset["uid"] == "INTRO" and asset["block_label"] == "引言2" for asset in assets)
    assert any(asset["uid"] == "PRICE_TRANSITION" and asset["block_label"] == "200元以下" for asset in assets)


def test_voice_asset_sync_matches_price_transition_version_labels_exactly(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    voice_root = tmp_path / "voice"
    account_dir = voice_root / "数码-键盘" / "小博"
    account_dir.mkdir(parents=True)
    paths = {
        "正文": account_dir / "0-价格-200元以下-正文.mp3",
        "正文2": account_dir / "0-价格-200元以下-正文2.mp3",
        "正文3": account_dir / "0-价格-200元以下-正文3-1.mp3",
    }
    for path in paths.values():
        path.write_bytes(b"voice")
    project_id = db.upsert_project({"name": "数码-键盘", "category_name": "键盘", "voice_root": str(voice_root)})
    parsed = parse_markdown_text(
        """
## 价格过渡文案

### 200元以下
#### 正文
第一版
#### 正文2
第二版
#### 正文3
第三版
""".strip()
    )
    SyncService(db).sync_markdown_payload(project_id, parsed)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小博', 'xiaobo', 'now', 'now')
            """
        )

    result = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)

    blocks = {block["block_label"]: block for block in repo.script_blocks(project_id)}
    assets = [
        asset
        for asset in repo.asset_bindings(project_id)
        if asset["asset_type"] == "voice" and asset["account_label"] == "小博" and asset["status"] == "ready"
    ]
    assert result["voice"] == 3
    assert result["unmatched"] == 0
    assert {
        Path(asset["path"]).name: (int(asset["script_block_id"]), asset["text_hash"])
        for asset in assets
    } == {
        path.name: (int(blocks[label]["id"]), blocks[label]["text_hash"])
        for label, path in paths.items()
    }


def test_voice_asset_sync_ignores_other_category_account_folders(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    voice_root = tmp_path / "voice"
    right = voice_root / "数码-键盘" / "小燃" / "0-价格-300-500元.wav"
    wrong = voice_root / "数码-有线耳机" / "小燃" / "0-价格-300-500元.wav"
    right.parent.mkdir(parents=True)
    wrong.parent.mkdir(parents=True)
    right.write_bytes(b"right")
    wrong.write_bytes(b"wrong")
    project_id = db.upsert_project({"name": "数码-键盘", "category_name": "键盘", "voice_root": str(voice_root)})
    parsed = parse_markdown_text(
        """
## 价格过渡文案

### 300-500元
这个价位
""".strip()
    )
    SyncService(db).sync_markdown_payload(project_id, parsed)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'now', 'now')
            """
        )

    result = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)

    assert result["voice"] == 1
    assert result["matched_items"][0]["path"] == str(right)
    assets = repo.asset_bindings(project_id)
    assert [asset["path"] for asset in assets if asset["asset_type"] == "voice"] == [str(right)]


def test_voice_asset_sync_marks_other_category_ready_binding_stale(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    voice_root = tmp_path / "voice"
    wrong = voice_root / "数码-有线耳机" / "小燃" / "0-价格-300-500元.wav"
    wrong.parent.mkdir(parents=True)
    wrong.write_bytes(b"wrong category")
    project_id = db.upsert_project({"name": "数码-键盘", "category_name": "键盘", "voice_root": str(voice_root)})
    parsed = parse_markdown_text(
        """
## 价格过渡文案

### 300-500元
这个价位
""".strip()
    )
    SyncService(db).sync_markdown_payload(project_id, parsed)
    block = repo.script_blocks(project_id)[0]
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
            VALUES (?, 'PRICE_TRANSITION', ?, 'voice', '小燃', 'xiaoran', '300-500元', ?, ?, ?, 'ready', 'scan', 1, 'now', 0, 'now', 'now')
            """,
            (project_id, block["id"], block["script_id"], block["text_hash"], str(wrong)),
        )

    result = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)

    assert result["voice"] == 0
    assert result["unmatched"] == 1
    assert [item["uid"] for item in result["removed_items"]] == ["PRICE_TRANSITION"]
    row = db.fetchone(
        "SELECT status FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(wrong)),
    )
    assert row["status"] == "stale"


def test_voice_asset_sync_marks_deleted_ready_voice_stale(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    voice_root = tmp_path / "voice"
    audio = voice_root / "数码-键盘" / "小燃" / "99-JP071-Alpha-正文.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"voice")
    project_id = db.upsert_project({"name": "数码-键盘", "category_name": "键盘", "voice_root": str(voice_root)})
    repo.upsert_products_from_master(project_id, [{"uid": "JP071", "title": "Alpha", "price_label": "99元"}])
    parsed = parse_markdown_text(
        """
## 商品文案

### Alpha-JP071-99元
#### 正文
新的正文
""".strip()
    )
    SyncService(db).sync_markdown_payload(project_id, parsed)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'now', 'now')
            """
        )

    first = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)
    audio.unlink()
    second = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)

    assert first["voice"] == 1
    assert first["unmatched"] == 0
    assert [item["uid"] for item in second["removed_items"]] == ["JP071"]
    assert second["unmatched"] == 1
    row = db.fetchone(
        "SELECT status FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(audio)),
    )
    assert row["status"] == "stale"


def test_manual_voice_binding_marks_current_script_ready_and_survives_resync(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    repo = Repository(db)
    voice_root = tmp_path / "voice"
    manual_audio = voice_root / "数码-键盘" / "小燃" / "external-audio.mp3"
    manual_audio.parent.mkdir(parents=True)
    manual_audio.write_bytes(b"manual voice")
    project_id = db.upsert_project({"name": "数码-键盘", "category_name": "键盘", "voice_root": str(voice_root)})
    parsed = parse_markdown_text(
        """
## 商品文案

### Alpha-JP071-99元
#### 正文
新的正文
""".strip()
    )
    repo.upsert_products_from_master(project_id, [{"uid": "JP071", "title": "Alpha", "price_label": "99元"}])
    SyncService(db).sync_markdown_payload(project_id, parsed)
    block = repo.script_blocks(project_id)[0]
    stale_audio = voice_root / "数码-键盘" / "小燃" / "old-JP071.wav"
    stale_audio.write_bytes(b"old voice")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO accounts (label, account_id, created_at, updated_at)
            VALUES ('小燃', 'xiaoran', 'now', 'now')
            """
        )
        conn.execute(
            """
            INSERT INTO asset_bindings
                (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash, path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
            VALUES (?, 'JP071', ?, 'voice', '小燃', 'xiaoran', '正文', ?, 'old-hash', ?, 'ready', 'generated', 1, 'now', 1, 'now', 'now')
            """,
            (project_id, block["id"], block["script_id"], str(stale_audio)),
        )

    result = SyncService(db).manual_bind_voice_asset(project_id, script_block_id=block["id"], account_label="小燃", path=manual_audio)

    assert result["source_kind"] == "manual"
    ready = db.fetchone(
        "SELECT * FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(manual_audio)),
    )
    old = db.fetchone(
        "SELECT status FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(stale_audio)),
    )
    assert ready["status"] == "ready"
    assert ready["source_kind"] == "manual"
    assert ready["confirmed"] == 1
    assert ready["text_hash"] == block["text_hash"]
    assert old["status"] == "expired"

    resync = SyncService(db).sync_assets(project_id, asset_type="voice", root_override=voice_root)

    preserved = db.fetchone(
        "SELECT status, source_kind FROM asset_bindings WHERE project_id=? AND path=?",
        (project_id, str(manual_audio)),
    )
    assert dict(preserved) == {"status": "ready", "source_kind": "manual"}
    assert resync["unmatched"] == 0
    assert resync["unmatched_items"] == []
