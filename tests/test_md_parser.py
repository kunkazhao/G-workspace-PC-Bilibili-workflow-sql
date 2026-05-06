from bworkflow_sql.md_parser import parse_markdown_text


def test_parse_multi_version_markdown():
    parsed = parse_markdown_text(
        """
## 引言文案

### 引言1
开场 A

### 引言2
开场 B

## 商品文案

### 竹林鸟夜莺Z1-YXEJ002-59元

#### 正文
商品正文 A

#### 版本2
商品正文 B

图片：G:\\a.png
视频：G:\\a.mp4

## 价格过渡文案

### 100-200元

#### 正文
过渡 A

#### 版本2
过渡 B

## 商品顺序
1. YXEJ002
""".strip()
    )
    assert [item.label for item in parsed.intro_scripts] == ["引言1", "引言2"]
    assert parsed.products[0].uid == "YXEJ002"
    assert [item.label for item in parsed.products[0].scripts] == ["正文", "版本2"]
    assert parsed.products[0].image_path == r"G:\a.png"
    assert parsed.price_transitions[0].label == "100-200元"
    assert [item.label for item in parsed.price_transitions[0].scripts] == ["正文", "版本2"]
    assert parsed.ordered_uids == ["YXEJ002"]


def test_parse_price_uid_title_heading():
    parsed = parse_markdown_text(
        """
## 商品文案

### 59元-YXEJ002-竹林鸟夜莺Z1
#### 正文
商品正文
""".strip()
    )
    assert parsed.products[0].price_label == "59元"
    assert parsed.products[0].uid == "YXEJ002"
    assert parsed.products[0].title == "竹林鸟夜莺Z1"
