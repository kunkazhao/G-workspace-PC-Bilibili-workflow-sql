from pathlib import Path

from bworkflow_sql.legacy_import import _candidate_roots, _product_from_path


def test_product_from_path_matches_uid_title_then_order():
    products = [
        {"uid": "PMGD010", "title": "机械师 ML3", "sort_order": 1},
        {"uid": "PMGD006", "title": "微星 B500PRO", "sort_order": 6},
        {"uid": "PMGD003", "title": "dpdupi 德普", "sort_order": 9},
    ]

    assert _product_from_path(Path("1-机械师 ML3_去字幕.mp4"), products)["uid"] == "PMGD010"
    assert _product_from_path(Path("6-微星B500PRO_去字幕.mp4"), products)["uid"] == "PMGD006"
    assert _product_from_path(Path("9-没有标题命中_去字幕.mp4"), products)["uid"] == "PMGD003"


def test_product_from_path_prefers_uid_token_boundaries():
    products = [
        {"uid": "LY018", "title": "瓷音未来Mars 2i", "sort_order": 7},
        {"uid": "RELY018", "title": "西圣 A1", "sort_order": 9},
    ]

    assert _product_from_path(Path("186元-RELY018-西圣 A1.mp4"), products)["uid"] == "RELY018"


def test_product_from_path_rejects_substring_uid_without_token_boundaries():
    products = [
        {"uid": "LY028", "title": "正确商品名", "sort_order": 16},
    ]

    assert _product_from_path(Path("499元-RELY028-水月雨梦回2.mp4"), products) == {}


def test_candidate_roots_support_category_aliases(tmp_path: Path):
    for name in ["有线耳机", "数码-有线耳机", "键盘大全", "鼠标垫"]:
        (tmp_path / name).mkdir()

    exact = _candidate_roots(tmp_path, parent_category="数码", category="有线耳机")
    contains = _candidate_roots(tmp_path, parent_category="数码", category="键盘", include_contains=True)

    assert [item.name for item in exact] == ["有线耳机", "数码-有线耳机"]
    assert [item.name for item in contains] == ["键盘大全"]
