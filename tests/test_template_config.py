from bworkflow_sql.template_config import (
    available_templates,
    display_video_slot_for_product_card_template_id,
    display_template_from_image_path,
    display_template_for_product_card_template_id,
    get_template_slot,
    get_remotion_template_metadata,
    image_set_for_template,
    resolve_product_card_template,
    user_for_template,
)


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


def test_display_template_from_image_path_uses_account_template_folder() -> None:
    path = r"G:\2026项目-b站\素材-商品ppt图片\数码-屏幕挂灯\荣荣\模板2\1399-PMGD001-明基 Halo2.png"

    assert display_template_from_image_path(path, account_label="荣荣") == "荣荣-模板2"


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


def test_xiaowai_remotion_template_1_is_available_ahead_of_legacy_template() -> None:
    templates = available_templates("小歪")

    assert templates[0] == "小歪模板1"
    assert "小歪-模板1" in templates
    assert user_for_template("小歪模板1") == "小歪"
    assert image_set_for_template("小歪模板1") == "模板1"
    assert display_template_for_product_card_template_id("muban-xiaowai-1") == "小歪模板1"


def test_xiaowai_remotion_template_1_video_slot_is_projected_from_metadata() -> None:
    metadata = get_remotion_template_metadata("muban-xiaowai-1")

    assert metadata["displayName"] == "小歪模板1"
    assert metadata["account"] == "小歪"
    assert metadata["coverMediaSlot"] == {
        "x": 22,
        "y": 168,
        "width": 496,
        "height": 279,
        "sourceWidth": 970,
        "sourceHeight": 480,
        "fitMode": "contain",
        "anchor": "center",
    }
    assert display_video_slot_for_product_card_template_id("muban-xiaowai-1") == {
        "x": 44,
        "y": 336,
        "width": 982,
        "height": 558,
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "coordinate_mode": "canvas_rect",
        "templateId": "muban-xiaowai-1",
        "templateVersion": "1.0.0",
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
    remotion_display_name = display_template_for_product_card_template_id("muban-xiaobo-1")
    templates = available_templates(user_for_template(remotion_display_name))

    assert templates[0] == remotion_display_name
    assert "小博-模板1" in templates
    assert "小博-模板2" in templates
    assert "小博-模板3" in templates
    assert len(templates) == 4
    assert user_for_template(remotion_display_name) == "小博"
    assert image_set_for_template(remotion_display_name) == "模板1"
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


def test_xiaoran_template_1_uses_calibrated_fixed_video_scale() -> None:
    assert get_template_slot("小燃-模板1") == {
        "x": -830,
        "y": -77,
        "width": 970,
        "height": 590,
        "coordinate_mode": "clip_transform_pixels",
        "scale_x": 970 / 1936,
        "scale_y": 590 / 1080,
    }


def test_xiaowai_template_2_uses_html_cover_stage_slot() -> None:
    assert get_template_slot("小歪-模板2") == {
        "x": -29,
        "y": 202,
        "width": 1132,
        "height": 676,
        "display_scale": 0.53,
    }


def test_muban_xiaobo_1_metadata_is_loaded_from_cutme_remotion_contract() -> None:
    metadata = get_remotion_template_metadata("muban-xiaobo-1")

    assert metadata["displayName"] == "小博模板1"
    assert metadata["account"] == "小博"
    assert metadata["templateVersion"] == "1.0.2"
    assert metadata["sourceCanvas"] == {"width": 970, "height": 480}
    assert metadata["cardPlacement"] == {
        "x": 0,
        "y": 0,
        "width": 1920,
        "height": 960,
        "anchor": "top",
        "bottomReserve": 120,
    }
    assert metadata["coverMediaSlot"] == {
        "x": 442,
        "y": 69,
        "width": 496,
        "height": 279,
        "sourceWidth": 970,
        "sourceHeight": 480,
        "fitMode": "contain",
        "anchor": "center",
    }


def test_muban_xiaobo_1_video_slot_is_projected_from_remotion_metadata() -> None:
    assert display_template_for_product_card_template_id("muban-xiaobo-1") == "小博模板1"
    assert display_video_slot_for_product_card_template_id("muban-xiaobo-1") == {
        "x": 875,
        "y": 138,
        "width": 982,
        "height": 558,
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "coordinate_mode": "canvas_rect",
        "templateId": "muban-xiaobo-1",
        "templateVersion": "1.0.2",
    }


def test_resolve_product_card_template_uses_explicit_template_before_account_default() -> None:
    by_id = resolve_product_card_template("小博", "muban-xiaobo-1")
    by_name = resolve_product_card_template("小博", "小博模板1")
    by_default = resolve_product_card_template("小博")

    assert by_id["templateId"] == "muban-xiaobo-1"
    assert by_name["templateId"] == "muban-xiaobo-1"
    assert by_default["templateId"] == "muban-xiaobo-1"


def test_resolve_product_card_template_can_require_explicit_still_template() -> None:
    try:
        resolve_product_card_template("小博", require_explicit=True)
    except ValueError as exc:
        assert "必须明确选择商品图模板" in str(exc)
    else:
        raise AssertionError("expected still/product-image flow to require explicit template")


def test_resolve_product_card_template_rejects_template_from_other_account() -> None:
    try:
        resolve_product_card_template("小燃", "muban-xiaobo-1")
    except ValueError as exc:
        assert "does not belong to account" in str(exc)
    else:
        raise AssertionError("expected template/account mismatch to fail")
