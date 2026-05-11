from bworkflow_sql.settings import DEFAULT_SPOKEN_MD_ROOT
from bworkflow_sql.ui import asset_folder_paths, manifest_account_label, manifest_product_video_gaps, parse_uid_list, voice_state


def test_parse_uid_list_accepts_chinese_and_english_commas_only():
    assert parse_uid_list("JP096, JP097，JP098") == [
        "JP096",
        "JP097",
        "JP098",
    ]


def test_spoken_markdown_default_root_is_spoken_copy_folder():
    assert str(DEFAULT_SPOKEN_MD_ROOT) == r"G:\WriteSpace\B站-文案脚本\10_b站文案\1.口播文案"


def test_manifest_account_label_prefers_manifest_then_entries():
    assert manifest_account_label({"account_label": "小燃", "entries": [{"account_label": "荣荣"}]}) == "小燃"
    assert manifest_account_label({"entries": [{"account_label": ""}, {"account_label": "小博"}]}) == "小博"
    assert manifest_account_label({"entries": []}) == ""


def test_manifest_product_video_gaps_reports_products_without_video():
    payload = {
        "entries": [
            {"type": "product", "product_uid": "A1", "product_name": "Alpha", "image_path": "a.png"},
            {"type": "product", "product_uid": "B2", "product_name": "Beta", "display_video_path": "b.mp4"},
            {"type": "transition", "product_uid": "PRICE_TRANSITION"},
        ]
    }

    assert manifest_product_video_gaps(payload) == ["A1 Alpha"]


def test_asset_folder_paths_prefer_current_category_user_and_global_video(tmp_path):
    image_root = tmp_path / "images"
    video_root = tmp_path / "videos"
    voice_root = tmp_path / "voices"
    current = image_root / "数码-键盘" / "小燃"
    old = image_root / "键盘" / "小燃"
    voice_dir = voice_root / "小燃-键盘"
    current.mkdir(parents=True)
    old.mkdir(parents=True)
    voice_dir.mkdir(parents=True)
    project = {
        "name": "数码-键盘",
        "category_name": "键盘",
        "image_root": str(image_root),
        "video_root": str(video_root),
        "voice_root": str(voice_root),
    }
    assets = [
        {"asset_type": "image", "status": "ready", "account_label": "小燃", "path": str(current / "模板1" / "JP001.png")},
        {"asset_type": "image", "status": "ready", "account_label": "小燃", "path": str(old / "模板1" / "JP002.png")},
        {"asset_type": "video", "status": "ready", "account_label": "", "path": str(video_root / "数码-键盘" / "小燃" / "JP001.mp4")},
    ]

    paths = asset_folder_paths(project, assets, "小燃")

    assert paths["image"] == str(current)
    assert paths["video"] == str(video_root / "数码-键盘")
    assert paths["voice"] == str(voice_dir)


def test_voice_state_treats_scanned_voice_without_hash_as_ready():
    assets = [
        {"uid": "JP071", "asset_type": "voice", "status": "ready", "account_label": "小燃", "text_hash": "old"},
        {"uid": "JP071", "asset_type": "voice", "status": "ready", "account_label": "小燃", "text_hash": ""},
    ]

    assert voice_state(assets, uid="JP071", account_label="小燃", hashes={"new"}) == "ready"
