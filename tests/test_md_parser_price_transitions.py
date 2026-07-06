from bworkflow_sql.md_parser import parse_markdown_text


def test_parse_empty_price_transition_headings():
    parsed = parse_markdown_text(
        """
## 价格过渡文案

### 200元以下

#### 正文1


### 200-400元

#### 正文1
""".strip()
    )

    assert [item.label for item in parsed.price_transitions] == ["200元以下", "200-400元"]
    assert [len(item.scripts) for item in parsed.price_transitions] == [0, 0]
