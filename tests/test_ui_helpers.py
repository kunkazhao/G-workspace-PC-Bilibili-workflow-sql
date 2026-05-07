from bworkflow_sql.settings import DEFAULT_SPOKEN_MD_ROOT
from bworkflow_sql.ui import manifest_account_label, manifest_product_video_gaps, parse_uid_list


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
