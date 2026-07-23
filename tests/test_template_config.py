import hashlib
import json
import os
from pathlib import Path

import pytest

import bworkflow_sql.template_config as template_config
from bworkflow_sql.template_config import (
    available_templates,
    display_video_slot_for_product_card_template_id,
    display_template_from_image_path,
    display_template_for_product_card_template_id,
    get_template_slot,
    get_remotion_template_metadata,
    image_set_for_template,
    product_card_text_capacity_certification_issues,
    resolve_product_card_template,
    user_for_template,
)


_cutme_metadata_env = os.environ.get("CUTME_REMOTION_TEMPLATE_METADATA")
_cutme_metadata_path = Path(_cutme_metadata_env) if _cutme_metadata_env else None
requires_cutme_metadata = pytest.mark.skipif(
    _cutme_metadata_path is None or not _cutme_metadata_path.is_file(),
    reason="set CUTME_REMOTION_TEMPLATE_METADATA for cross-repository metadata tests",
)


@pytest.fixture(autouse=True)
def _clear_template_metadata_caches():
    for name in ("_remotion_template_contract", "_remotion_template_metadata"):
        cached = getattr(template_config, name, None)
        if cached is not None:
            cached.cache_clear()
    yield
    for name in ("_remotion_template_contract", "_remotion_template_metadata"):
        cached = getattr(template_config, name, None)
        if cached is not None:
            cached.cache_clear()


def _minimal_template_contract() -> dict:
    return {
        "slotRegistry": {
            "title": {"type": "text", "source": "dataMap.title"},
            "displayPrice": {"type": "text", "source": "dataMap.displayPrice"},
            "specs": {"type": "label_value_list", "source": "slots"},
            "review": {"type": "text", "source": "dataMap.review"},
            "priceBandLabel": {"type": "text", "source": "dataMap.priceBandLabel"},
            "categoryLabel": {"type": "text", "source": "dataMap.categoryLabel"},
            "productMedia": {"type": "media", "source": "coverAsset"},
        },
        "templates": [
            {
                "templateId": "muban-test-1",
                "slotDeclarations": [
                    {"key": "title", "required": True},
                    {"key": "displayPrice", "required": True},
                    {"key": "specs", "required": False, "emptyPolicy": "preserve"},
                    {"key": "review", "required": False, "emptyPolicy": "preserve"},
                    {"key": "priceBandLabel", "required": True},
                    {"key": "categoryLabel", "required": False, "emptyPolicy": "preserve"},
                    {"key": "productMedia", "required": True},
                ],
            }
        ],
    }


def _write_template_contract(
    tmp_path: Path,
    monkeypatch,
    mutate,
) -> dict:
    payload = _minimal_template_contract()
    mutate(payload)
    metadata_path = tmp_path / "cutme-remotion" / "product-card-templates.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(template_config, "REMOTION_TEMPLATE_METADATA_PATH", metadata_path)
    return payload


def _sha256(path: Path) -> str:
    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def test_text_capacity_certification_is_hash_bound(tmp_path, monkeypatch) -> None:
    renderer_root = tmp_path / "remotion-renderer"
    source = renderer_root / "src" / "components" / "product-card.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export const Card = () => null;\n", encoding="utf-8")
    supporting_source = source.parent / "MeasuredFitText.tsx"
    supporting_source.write_text("export const Fit = () => null;\n", encoding="utf-8")
    baseline = renderer_root / "product-card-text-capacity-baseline.json"
    baseline.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
    metadata_path = renderer_root / "product-card-templates.json"
    metadata_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(template_config, "REMOTION_TEMPLATE_METADATA_PATH", metadata_path)
    metadata = {
        "templateId": "muban-test-1",
        "templateVersion": "1.2.0",
        "textCapacityCertification": {
            "status": "approved",
            "templateVersion": "1.2.0",
            "baselineSchemaVersion": 1,
            "baselineSha256": _sha256(baseline),
            "componentSource": "src/components/product-card.tsx",
            "sourceSha256": _sha256(source),
            "supportingSources": [
                {
                    "path": "src/components/MeasuredFitText.tsx",
                    "sha256": _sha256(supporting_source),
                }
            ],
        },
    }

    assert product_card_text_capacity_certification_issues(metadata) == []

    source.write_bytes(b"export const Card = () => null;\r\n")
    supporting_source.write_bytes(b"export const Fit = () => null;\r\n")
    assert product_card_text_capacity_certification_issues(metadata) == []

    source.write_text("export const Card = () => 'changed';\n", encoding="utf-8")
    issues = product_card_text_capacity_certification_issues(metadata)
    assert [item["code"] for item in issues] == ["text_capacity_source_hash_mismatch"]

    source.write_text("export const Card = () => null;\n", encoding="utf-8")
    supporting_source.write_text("export const Fit = () => 'changed';\n", encoding="utf-8")
    issues = product_card_text_capacity_certification_issues(metadata)
    assert [item["code"] for item in issues] == [
        "text_capacity_supporting_source_hash_mismatch"
    ]


def test_text_capacity_certification_is_required() -> None:
    issues = product_card_text_capacity_certification_issues(
        {"templateId": "muban-test-1", "templateVersion": "1.0.0"}
    )

    assert [item["code"] for item in issues] == ["text_capacity_uncertified"]


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


@requires_cutme_metadata
def test_rongrong_template_preset_available() -> None:
    templates = available_templates("荣荣")

    assert templates[0] == "荣荣模板1"
    assert "荣荣-模板1" in templates
    assert "荣荣-模板2" in templates
    assert user_for_template("荣荣模板1") == "荣荣"
    assert user_for_template("荣荣-模板1") == "荣荣"
    assert user_for_template("荣荣-模板2") == "荣荣"
    assert image_set_for_template("荣荣模板1") == "模板1"
    assert image_set_for_template("荣荣-模板1") == "模板1"
    assert image_set_for_template("荣荣-模板2") == "模板2"
    assert display_template_for_product_card_template_id("muban-rongrong-1") == "荣荣模板1"
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


@requires_cutme_metadata
def test_rongrong_remotion_template_1_video_slot_is_projected_from_metadata() -> None:
    metadata = get_remotion_template_metadata("muban-rongrong-1")

    assert metadata["displayName"] == "荣荣模板1"
    assert metadata["account"] == "荣荣"
    assert metadata["coverMediaSlot"] == {
        "x": 39,
        "y": 20,
        "width": 515,
        "height": 290,
        "sourceWidth": 970,
        "sourceHeight": 480,
        "fitMode": "contain",
        "anchor": "center",
    }
    assert display_video_slot_for_product_card_template_id("muban-rongrong-1") == {
        "x": 77,
        "y": 40,
        "width": 1019,
        "height": 580,
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "coordinate_mode": "canvas_rect",
        "templateId": "muban-rongrong-1",
        "templateVersion": "2.1.1",
        "display_scale": 0.52,
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


@requires_cutme_metadata
def test_xiaowai_remotion_template_1_is_available_ahead_of_legacy_template() -> None:
    templates = available_templates("小歪")

    assert templates[0] == "小歪模板1"
    assert "小歪-模板1" in templates
    assert user_for_template("小歪模板1") == "小歪"
    assert image_set_for_template("小歪模板1") == "模板1"
    assert display_template_for_product_card_template_id("muban-xiaowai-1") == "小歪模板1"


@requires_cutme_metadata
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
        "templateVersion": "1.1.1",
        "display_scale": 0.51,
    }


def test_xiaobo_template_2_uses_html_cover_frame_slot() -> None:
    assert get_template_slot("小博-模板2") == {
        "x": 1015,
        "y": 154,
        "width": 680,
        "height": 520,
        "display_scale": 0.52,
    }


@requires_cutme_metadata
def test_xiaobo_template_3_uses_html_cover_frame_slot() -> None:
    remotion_display_name = display_template_for_product_card_template_id("muban-xiaobo-1")
    templates = available_templates(user_for_template(remotion_display_name))

    assert templates[0] == remotion_display_name
    assert "小博-模板1" in templates
    assert "小博-模板2" in templates
    assert "小博-模板3" in templates
    assert templates.index(display_template_for_product_card_template_id("muban-xiaobo-2")) == 1
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


@requires_cutme_metadata
def test_xiaoran_template_2_uses_jianying_ui_coordinates() -> None:
    templates = available_templates("小燃")

    assert templates[0] == "小燃模板1"
    assert "小燃-模板1" in templates
    assert "小燃-模板2" in templates
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


@requires_cutme_metadata
def test_xiaoran_remotion_template_1_is_available_ahead_of_legacy_template() -> None:
    templates = available_templates("小燃")

    assert templates[0] == "小燃模板1"
    assert "小燃-模板1" in templates
    assert user_for_template("小燃模板1") == "小燃"
    assert image_set_for_template("小燃模板1") == "模板1"
    assert display_template_for_product_card_template_id("muban-xiaoran-1") == "小燃模板1"


@requires_cutme_metadata
def test_xiaoran_remotion_template_1_video_slot_is_projected_from_metadata() -> None:
    metadata = get_remotion_template_metadata("muban-xiaoran-1")

    assert metadata["displayName"] == "小燃模板1"
    assert metadata["account"] == "小燃"
    assert metadata["coverMediaSlot"] == {
        "x": 30,
        "y": 158,
        "width": 496,
        "height": 279,
        "sourceWidth": 970,
        "sourceHeight": 480,
        "fitMode": "contain",
        "anchor": "center",
    }
    assert display_video_slot_for_product_card_template_id("muban-xiaoran-1") == {
        "x": 59,
        "y": 316,
        "width": 982,
        "height": 558,
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "coordinate_mode": "canvas_rect",
        "templateId": "muban-xiaoran-1",
        "templateVersion": "1.1.1",
    }


@requires_cutme_metadata
def test_xiaoran_remotion_template_2_keeps_calibrated_display_scale() -> None:
    slot = display_video_slot_for_product_card_template_id("muban-xiaoran-2")

    assert slot["templateId"] == "muban-xiaoran-2"
    assert slot["coordinate_mode"] == "canvas_rect"
    assert slot["x"] == 63
    assert slot["y"] == 332
    assert slot["display_scale"] == 0.55


def test_xiaowai_template_2_uses_html_cover_stage_slot() -> None:
    assert get_template_slot("小歪-模板2") == {
        "x": -29,
        "y": 202,
        "width": 1132,
        "height": 676,
        "display_scale": 0.53,
    }


@requires_cutme_metadata
def test_muban_rongrong_2_uses_right_aligned_16_by_9_video_slot_for_calibration() -> None:
    metadata = get_remotion_template_metadata("muban-rongrong-2")
    assert metadata["coverMediaSlot"] == {
        "x": 549,
        "y": 62,
        "width": 412,
        "height": 340,
        "sourceWidth": 970,
        "sourceHeight": 480,
        "fitMode": "contain",
        "anchor": "center",
    }
    assert metadata["videoOverlaySlot"]["clearSlot"] == {
        "x": 558,
        "y": 62,
        "width": 412,
        "height": 340,
        "sourceWidth": 970,
        "sourceHeight": 480,
    }
    assert metadata["videoOverlaySlot"]["clearColour"] == "0xe7f1ff"

    assert display_video_slot_for_product_card_template_id("muban-rongrong-2") == {
        "x": 1136,
        "y": 244,
        "width": 784,
        "height": 440,
        "sourceWidth": 1920,
        "sourceHeight": 1080,
        "coordinate_mode": "canvas_rect",
        "templateId": "muban-rongrong-2",
        "templateVersion": "1.2.1",
    }


@requires_cutme_metadata
def test_muban_xiaobo_1_metadata_is_loaded_from_cutme_remotion_contract() -> None:
    metadata = get_remotion_template_metadata("muban-xiaobo-1")

    assert metadata["displayName"] == "小博模板1"
    assert metadata["account"] == "小博"
    assert metadata["templateVersion"] == "1.1.1"
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


@requires_cutme_metadata
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
        "templateVersion": "1.1.1",
        "display_scale": 0.52,
    }


@requires_cutme_metadata
def test_resolve_product_card_template_uses_explicit_template_before_account_default() -> None:
    by_id = resolve_product_card_template("小博", "muban-xiaobo-1")
    by_name = resolve_product_card_template("小博", "小博模板1")
    by_default = resolve_product_card_template("小博")

    assert by_id["templateId"] == "muban-xiaobo-1"
    assert by_name["templateId"] == "muban-xiaobo-1"
    assert by_default["templateId"] == "muban-xiaobo-1"


@requires_cutme_metadata
def test_resolve_product_card_template_can_require_explicit_still_template() -> None:
    try:
        resolve_product_card_template("小博", require_explicit=True)
    except ValueError as exc:
        assert "必须明确选择商品图模板" in str(exc)
    else:
        raise AssertionError("expected still/product-image flow to require explicit template")


@requires_cutme_metadata
def test_resolve_product_card_template_rejects_template_from_other_account() -> None:
    try:
        resolve_product_card_template("小燃", "muban-xiaobo-1")
    except ValueError as exc:
        assert "does not belong to account" in str(exc)
    else:
        raise AssertionError("expected template/account mismatch to fail")


def test_new_registered_optional_slot_does_not_require_template_declaration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = _write_template_contract(
        tmp_path,
        monkeypatch,
        lambda contract: contract["slotRegistry"].update(
            {
                "futureBadge": {
                    "type": "text",
                    "source": "dataMap.futureBadge",
                }
            }
        ),
    )

    registry = template_config.get_remotion_slot_registry()
    metadata = get_remotion_template_metadata(payload["templates"][0]["templateId"])

    assert registry["futureBadge"]["type"] == "text"
    assert "futureBadge" not in {item["key"] for item in metadata["slotDeclarations"]}


def test_template_declaration_rejects_unregistered_slot_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def mutate(contract: dict) -> None:
        contract["templates"][0]["slotDeclarations"].append(
            {"key": "notRegistered", "required": False, "emptyPolicy": "preserve"}
        )

    payload = _write_template_contract(tmp_path, monkeypatch, mutate)

    with pytest.raises(ValueError, match="notRegistered.*slotRegistry"):
        get_remotion_template_metadata(payload["templates"][0]["templateId"])


def test_slot_contract_rejects_invalid_registry_and_declaration_shapes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invalid_contracts: list[tuple[dict, str]] = []

    unknown_type = _minimal_template_contract()
    unknown_type["slotRegistry"]["title"]["type"] = "unknown"
    invalid_contracts.append((unknown_type, "slot type"))

    blank_source = _minimal_template_contract()
    blank_source["slotRegistry"]["title"]["source"] = "   "
    invalid_contracts.append((blank_source, "source"))

    non_boolean_required = _minimal_template_contract()
    non_boolean_required["templates"][0]["slotDeclarations"][0]["required"] = 1
    invalid_contracts.append((non_boolean_required, "required"))

    bad_empty_policy = _minimal_template_contract()
    bad_empty_policy["templates"][0]["slotDeclarations"][2]["emptyPolicy"] = "collapse"
    invalid_contracts.append((bad_empty_policy, "emptyPolicy"))

    missing_optional_policy = _minimal_template_contract()
    missing_optional_policy["templates"][0]["slotDeclarations"][2].pop("emptyPolicy")
    invalid_contracts.append((missing_optional_policy, "emptyPolicy"))

    required_with_policy = _minimal_template_contract()
    required_with_policy["templates"][0]["slotDeclarations"][0]["emptyPolicy"] = "preserve"
    invalid_contracts.append((required_with_policy, "emptyPolicy"))

    malformed_declaration = _minimal_template_contract()
    malformed_declaration["templates"][0]["slotDeclarations"][0] = "bad"
    invalid_contracts.append((malformed_declaration, "declaration"))

    duplicate_template_id = _minimal_template_contract()
    duplicate_template_id["templates"].append(
        json.loads(json.dumps(duplicate_template_id["templates"][0]))
    )
    invalid_contracts.append((duplicate_template_id, "templateId"))

    non_string_template_id = _minimal_template_contract()
    non_string_template_id["templates"][0]["templateId"] = 123
    invalid_contracts.append((non_string_template_id, "templateId"))

    spaced_template_id = _minimal_template_contract()
    spaced_template_id["templates"][0]["templateId"] = " muban-test-1"
    invalid_contracts.append((spaced_template_id, "templateId"))

    spaced_registry_key = _minimal_template_contract()
    title_definition = spaced_registry_key["slotRegistry"].pop("title")
    spaced_registry_key["slotRegistry"][" title"] = title_definition
    invalid_contracts.append((spaced_registry_key, "slotRegistry"))

    spaced_source = _minimal_template_contract()
    spaced_source["slotRegistry"]["title"]["source"] = "dataMap.title "
    invalid_contracts.append((spaced_source, "source"))

    spaced_declaration_key = _minimal_template_contract()
    spaced_declaration_key["templates"][0]["slotDeclarations"][0]["key"] = "title "
    invalid_contracts.append((spaced_declaration_key, "declaration"))

    non_string_declaration_key = _minimal_template_contract()
    non_string_declaration_key["templates"][0]["slotDeclarations"][0]["key"] = 123
    invalid_contracts.append((non_string_declaration_key, "declaration"))

    for index, (payload, message) in enumerate(invalid_contracts):
        metadata_path = tmp_path / f"invalid-{index}.json"
        metadata_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(template_config, "REMOTION_TEMPLATE_METADATA_PATH", metadata_path)
        template_config._remotion_template_contract.cache_clear()
        template_config._remotion_template_metadata.cache_clear()
        with pytest.raises(ValueError, match=message):
            template_config.get_remotion_slot_registry()


def test_existing_unreadable_or_invalid_metadata_fails_closed_with_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    invalid_json_path = tmp_path / "invalid.json"
    invalid_json_path.write_text("{", encoding="utf-8")
    unreadable_path = tmp_path / "metadata-directory"
    unreadable_path.mkdir()

    for metadata_path in (invalid_json_path, unreadable_path):
        monkeypatch.setattr(
            template_config, "REMOTION_TEMPLATE_METADATA_PATH", metadata_path
        )
        template_config._remotion_template_contract.cache_clear()
        template_config._remotion_template_metadata.cache_clear()
        with pytest.raises(ValueError) as exc_info:
            template_config.get_remotion_slot_registry()
        assert str(metadata_path) in str(exc_info.value)


def test_required_label_value_list_rejects_empty_or_blank_items(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def require_specs(contract: dict) -> None:
        specs = next(
            item
            for item in contract["templates"][0]["slotDeclarations"]
            if item["key"] == "specs"
        )
        specs["required"] = True
        specs.pop("emptyPolicy")

    payload = _write_template_contract(tmp_path, monkeypatch, require_specs)
    template_id = payload["templates"][0]["templateId"]
    product_card = {
        "dataMap": {
            "title": "Alpha Keyboard",
                "displayPrice": "299元",
            "priceBandLabel": "200-300元",
        },
        "coverAsset": "assets/covers/P001.png",
    }

    invalid_values = (
        [{}],
        [{"label": " ", "value": "机械轴"}],
        [{"label": "轴体", "value": " "}],
    )
    for invalid_specs in invalid_values:
        product_card["slots"] = invalid_specs
        issues = template_config.product_card_slot_issues(template_id, product_card)
        assert "specs" in [item["slot_key"] for item in issues]

    product_card["slots"] = [{"label": "轴体", "value": "机械轴"}]
    assert template_config.product_card_slot_issues(template_id, product_card) == []


def test_only_missing_required_slot_values_are_blocking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    payload = _write_template_contract(
        tmp_path, monkeypatch, lambda _contract: None
    )
    template_id = payload["templates"][0]["templateId"]
    product_card = {
        "dataMap": {
            "title": "Alpha Keyboard",
                "displayPrice": "299元",
            "priceBandLabel": "200-300元",
        },
        "coverAsset": "assets/covers/P001.png",
    }

    assert template_config.product_card_slot_issues(template_id, product_card) == []

    product_card["dataMap"]["title"] = ""
    issues = template_config.product_card_slot_issues(template_id, product_card)

    assert [item["slot_key"] for item in issues] == ["title"]
    assert all(item["blocking"] is True for item in issues)
