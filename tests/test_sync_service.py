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

    class FakeMasterSchemes:
        @staticmethod
        def fetch_scheme_summary(*, workspace_id, scheme_id, force_refresh=False):
            calls.append(
                {
                    "workspace_id": workspace_id,
                    "scheme_id": scheme_id,
                    "force_refresh": force_refresh,
                }
            )
            return {"items": [{"uid": "JP096", "title": "狼蛛F75Max 客制化", "price": "279元"}]}

    monkeypatch.setattr("bworkflow_sql.sync_service.install_legacy_paths", lambda: None)
    monkeypatch.setattr("bworkflow_sql.sync_service.try_import", lambda name: FakeMasterSchemes)

    result = SyncService(db).sync_master_scheme(project_id, apply_changes=False)

    assert calls == [{"workspace_id": "workspace-1", "scheme_id": "scheme-1", "force_refresh": True}]
    assert result["added"] == [{"uid": "JP096", "title": "狼蛛F75Max 客制化", "price_label": "279元", "master_item_id": "", "sort_order": 1}]


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
