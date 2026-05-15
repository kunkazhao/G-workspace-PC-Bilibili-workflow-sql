from pathlib import Path

from bworkflow_sql.copy_writer import parse_uid_copy_blocks, preview_copy_write, write_copy_blocks_to_markdown


def test_parse_uid_copy_blocks_splits_by_product_uid():
    blocks = parse_uid_copy_blocks(
        """
商品UID: XLB006
第一段文案。

商品UID：XLB008
第二段文案。
""".strip()
    )

    assert [(item.uid, item.body) for item in blocks] == [
        ("XLB006", "第一段文案。"),
        ("XLB008", "第二段文案。"),
    ]


def test_write_copy_blocks_fills_empty_body_and_appends_existing_copy(tmp_path: Path):
    path = tmp_path / "copy.md"
    path.write_text(
        """
## 商品文案

### 199元-XLB006-京东京造蚕丝被

#### 正文

### 239元-XLB008-无印良品100%蚕丝被

#### 正文

已有文案。
""".lstrip(),
        encoding="utf-8",
    )
    products = [
        {"uid": "XLB006", "title": "京东京造蚕丝被"},
        {"uid": "XLB008", "title": "无印良品100%蚕丝被"},
    ]
    text = """
商品UID: XLB006
新文案一。

商品UID: XLB008
新文案二。
""".strip()

    result = write_copy_blocks_to_markdown(path, text, products)

    output = path.read_text(encoding="utf-8")
    assert result["written"] == [{"uid": "XLB006", "label": "正文"}, {"uid": "XLB008", "label": "正文2"}]
    assert "#### 正文\n\n新文案一。" in output
    assert "#### 正文\n\n已有文案。\n\n#### 正文2\n\n新文案二。" in output


def test_preview_copy_write_reports_unmatched_inputs(tmp_path: Path):
    path = tmp_path / "copy.md"
    path.write_text(
        """
## 商品文案

### 199元-XLB006-京东京造蚕丝被

#### 正文
""".lstrip(),
        encoding="utf-8",
    )
    products = [{"uid": "XLB006"}, {"uid": "XLB008"}]

    result = preview_copy_write(
        path,
        """
商品UID: XLB006
可写入。

商品UID: XLB008
缺标题。

商品UID: XLB999
不在项目。
""".strip(),
        products,
    )

    assert [item["uid"] for item in result["matched"]] == ["XLB006"]
    assert result["missing_heading"] == ["XLB008"]
    assert result["missing_product"] == ["XLB999"]
