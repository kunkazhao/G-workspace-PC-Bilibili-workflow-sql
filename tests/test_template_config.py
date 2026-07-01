from bworkflow_sql.template_config import available_templates, get_template_slot, image_set_for_template, user_for_template


def test_zhiliao_template_preset_available() -> None:
    assert available_templates("知了") == ["知了-模板1"]
    assert user_for_template("知了-模板1") == "知了"
    assert image_set_for_template("知了-模板1") == "模板1"
    assert get_template_slot("知了-模板1") == {
        "x": 67,
        "y": 185,
        "width": 990,
        "height": 576,
    }


def test_rongrong_template_preset_available() -> None:
    assert available_templates("荣荣") == ["荣荣-模板1", "荣荣-模板2"]
    assert user_for_template("荣荣-模板1") == "荣荣"
    assert user_for_template("荣荣-模板2") == "荣荣"
    assert image_set_for_template("荣荣-模板1") == "模板1"
    assert image_set_for_template("荣荣-模板2") == "模板2"
    assert get_template_slot("荣荣-模板1") == {
        "x": 115,
        "y": 200,
        "width": 941,
        "height": 554,
    }
    assert get_template_slot("荣荣-模板2") == {
        "x": 44,
        "y": 172,
        "width": 851,
        "height": 436,
        "display_scale": 0.42,
    }


def test_hyphen_template_still_uses_template_suffix() -> None:
    assert image_set_for_template("小歪-模板2") == "模板2"


def test_xiaowai_template_1_uses_jianying_panel_coordinates() -> None:
    assert get_template_slot("小歪-模板1") == {
        "x": -855,
        "y": -22,
        "width": 960,
        "height": 540,
        "coordinate_mode": "clip_transform_pixels",
    }


def test_xiaobo_template_2_uses_html_cover_frame_slot() -> None:
    assert get_template_slot("小博-模板2") == {
        "x": 1015,
        "y": 154,
        "width": 680,
        "height": 520,
        "display_scale": 0.52,
    }


def test_xiaobo_template_3_uses_html_cover_frame_slot() -> None:
    assert available_templates("小博") == ["小博-模板1", "小博-模板2", "小博-模板3"]
    assert user_for_template("小博-模板3") == "小博"
    assert image_set_for_template("小博-模板3") == "模板3"
    assert get_template_slot("小博-模板3") == {
        "x": 1015,
        "y": 154,
        "width": 680,
        "height": 520,
        "display_scale": 0.52,
    }


def test_xiaoran_template_2_uses_jianying_ui_coordinates() -> None:
    assert available_templates("小燃") == ["小燃-模板1", "小燃-模板2"]
    assert user_for_template("小燃-模板2") == "小燃"
    assert image_set_for_template("小燃-模板2") == "模板2"
    assert get_template_slot("小燃-模板2") == {
        "x": 47,
        "y": 317,
        "width": 1003,
        "height": 588,
        "display_scale": 0.55,
    }


def test_xiaowai_template_2_uses_html_cover_stage_slot() -> None:
    assert get_template_slot("小歪-模板2") == {
        "x": -29,
        "y": 202,
        "width": 1132,
        "height": 676,
        "display_scale": 0.53,
    }
