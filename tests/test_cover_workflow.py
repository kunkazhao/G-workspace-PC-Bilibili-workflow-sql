import json
from pathlib import Path

import pytest
from PIL import Image

from bworkflow_sql.artifact_approvals import build_artifact_approval, sha256_file
from bworkflow_sql.cover_workflow import (
    confirm_cover_copy,
    confirm_cover_image,
    cover_context,
    prepare_cover_generation,
    record_cover_copy_options,
    record_cover_image,
    reject_cover_image,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fixture(tmp_path: Path, monkeypatch, *, confirmed: bool = True) -> tuple[Path, Path]:
    import bworkflow_sql.cover_workflow as module

    workspace = tmp_path / "workspace"
    portraits = tmp_path / "portraits"
    portraits.mkdir()
    (portraits / "荣荣.jpg").write_bytes(b"fixed portrait")
    config = tmp_path / "cover-prompts.json"
    _write_json(
        config,
        {
            "portraitRoot": str(portraits),
            "categoryVisualGuidance": {"家居-防晒衣": "所有商品必须是防晒衣，不得混入雨衣。"},
            "accounts": {
                "荣荣": {
                    "styleId": "rongrong-home-v1",
                    "styleVersion": "1.0.0",
                    "portraitFilename": "荣荣.jpg",
                    "compositionVariants": [
                        {"id": "layout-a", "prompt": "人物在左，商品错落分布。"},
                        {"id": "layout-b", "prompt": "人物在右，商品纵深分布。"},
                    ],
                    "promptTemplate": "4:3 家居杂志封面，品类 {category}，多个该品类商品，形态 {category_visual_guidance}，构图 {composition_variant}，文字必须逐字为：{cover_copy}，只生成一张。",
                }
            },
        },
    )
    monkeypatch.setattr(module, "INTERNAL_WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(module, "COVER_PROMPTS_PATH", config)

    package = tmp_path / "render-package.json"
    _write_json(package, {"segments": [{"type": "intro", "spokenText": "防晒衣怎么选"}]})
    spoken = tmp_path / "完整口播稿.md"
    spoken.write_text("# 防晒衣完整口播稿\n\n这是完整内容。\n", encoding="utf-8")
    manifest = tmp_path / "final-video.run-manifest.json"
    _write_json(
        manifest,
        {
            "schemaVersion": "1.1.0",
            "kind": "bworkflow.final_video_run",
            "spoken_script": {
                "snapshot_path": str(spoken),
                "snapshot_sha256": sha256_file(spoken),
                "render_package_path": str(package),
                "render_package_sha256": sha256_file(package),
            },
        },
    )
    final_mp4 = tmp_path / "完整成片.mp4"
    final_mp4.write_bytes(b"complete mp4")
    approval = build_artifact_approval(
        "full_mp4",
        final_mp4,
        approved_at="2026-07-20T12:00:00+08:00",
        source_revision=sha256_file(manifest),
    )
    pipeline = tmp_path / ".pipeline.json"
    _write_json(
        pipeline,
        {
            "bworkflow_project_id": 18,
            "category": "家居-防晒衣",
            "account": "荣荣",
            "output_dir": str(tmp_path / "delivery"),
            "phases": {"assembly": {"run_manifest_path": str(manifest)}},
            "production_confirmation": {
                "status": "confirmed" if confirmed else "pending",
                "run_manifest_path": str(manifest),
            },
            "artifact_approvals": {"full_mp4": approval},
        },
    )
    return pipeline, manifest


def _options_file(tmp_path: Path) -> Path:
    path = tmp_path / "options.json"
    _write_json(path, ["防晒衣别乱买", "今年怎么选", "十四件实测", "通勤防晒指南", "夏天穿这几件"])
    return path


def test_cover_context_requires_formal_production_confirmation(tmp_path: Path, monkeypatch) -> None:
    pipeline, _ = _fixture(tmp_path, monkeypatch, confirmed=False)

    with pytest.raises(ValueError, match="formal production"):
        cover_context(pipeline)


def test_cover_flow_freezes_copy_prompt_portrait_and_accepts_one_image(tmp_path: Path, monkeypatch) -> None:
    pipeline, _ = _fixture(tmp_path, monkeypatch)

    context = cover_context(pipeline)
    assert context["cover_copy_constraints"]["candidate_count"] == 5
    assert "完整内容" in context["spoken_script"]
    record_cover_copy_options(pipeline, options_file=_options_file(tmp_path))
    confirm_cover_copy(pipeline, index=2)
    prepared = prepare_cover_generation(pipeline)
    package_path = Path(prepared["cover_package_path"])
    package = json.loads(package_path.read_text(encoding="utf-8"))
    assert package["selectedCopy"] == "今年怎么选"
    assert package["styleId"] == "rongrong-home-v1"
    assert package["imageRequirements"] == {
        "aspectRatio": "4:3",
        "candidateCount": 1,
        "textMode": "model_native",
    }
    assert "家居-防晒衣" in package["prompt"]
    assert "今年怎么选" in package["prompt"]
    assert "所有商品必须是防晒衣" in package["prompt"]
    assert package["compositionVariantId"] in {"layout-a", "layout-b"}
    assert package["compositionVariant"] in package["prompt"]
    assert Path(package["portraitSnapshotPath"]).read_bytes() == b"fixed portrait"

    generated = tmp_path / "generated.png"
    Image.new("RGB", (1200, 900), "white").save(generated)
    record_cover_image(pipeline, cover_package_path=package_path, image_path=generated)
    accepted = confirm_cover_image(pipeline)

    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    final_cover = Path(accepted["approval"]["path"])
    assert final_cover.is_file()
    assert payload["phases"]["cover"]["status"] == "accepted"
    assert payload["current_phase"] == "publishing"
    assert payload["next_action"] == "prepare_publishing_assets"
    assert payload["artifact_approvals"]["cover_image"]["sha256"] == sha256_file(final_cover)


def test_cover_reject_preserves_copy_and_creates_new_attempt(tmp_path: Path, monkeypatch) -> None:
    pipeline, _ = _fixture(tmp_path, monkeypatch)
    record_cover_copy_options(pipeline, options_file=_options_file(tmp_path))
    confirm_cover_copy(pipeline, index=1)
    first = prepare_cover_generation(pipeline)
    image = tmp_path / "generated.png"
    Image.new("RGB", (800, 600), "black").save(image)
    record_cover_image(pipeline, cover_package_path=first["cover_package_path"], image_path=image)
    reject_cover_image(pipeline, reason="人物不像")
    second = prepare_cover_generation(pipeline)

    payload = json.loads(pipeline.read_text(encoding="utf-8"))
    assert first["attempt_id"] != second["attempt_id"]
    assert first["composition_variant_id"] != second["composition_variant_id"]
    assert payload["phases"]["cover"]["selected_copy"] == "防晒衣别乱买"
    assert payload["phases"]["cover"]["status"] == "generation_ready"


def test_cover_rejects_wrong_ratio_and_tampered_package(tmp_path: Path, monkeypatch) -> None:
    pipeline, _ = _fixture(tmp_path, monkeypatch)
    record_cover_copy_options(pipeline, options_file=_options_file(tmp_path))
    confirm_cover_copy(pipeline, index=1)
    prepared = prepare_cover_generation(pipeline)
    wrong_ratio = tmp_path / "wrong.png"
    Image.new("RGB", (1000, 700), "white").save(wrong_ratio)
    with pytest.raises(ValueError, match="must be 4:3"):
        record_cover_image(pipeline, cover_package_path=prepared["cover_package_path"], image_path=wrong_ratio)

    package_path = Path(prepared["cover_package_path"])
    package_path.write_text(package_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    right_ratio = tmp_path / "right.png"
    Image.new("RGB", (400, 300), "white").save(right_ratio)
    with pytest.raises(ValueError, match="package has changed"):
        record_cover_image(pipeline, cover_package_path=package_path, image_path=right_ratio)


def test_production_config_has_four_distinct_account_styles_and_portraits() -> None:
    config_path = Path(__file__).parents[1] / "config" / "cover-prompts.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    accounts = payload["accounts"]
    assert set(accounts) == {"小燃", "小博", "小歪", "荣荣"}
    assert len({item["styleId"] for item in accounts.values()}) == 4
    assert len({item["promptTemplate"] for item in accounts.values()}) == 4
    assert {item["portraitFilename"] for item in accounts.values()} == {
        "小燃.jpg",
        "小博.jpg",
        "小歪.jpg",
        "荣荣.jpg",
    }
    for item in accounts.values():
        assert "{category}" in item["promptTemplate"]
        assert "{cover_copy}" in item["promptTemplate"]
        assert "4:3" in item["promptTemplate"]
    xiaobo = accounts["小博"]
    assert xiaobo["styleVersion"] == "1.1.0"
    assert "{composition_variant}" in xiaobo["promptTemplate"]
    assert "{category_visual_guidance}" in xiaobo["promptTemplate"]
    assert len(xiaobo["compositionVariants"]) == 5
    assert len({item["id"] for item in xiaobo["compositionVariants"]}) == 5
    assert "Soundbar" in payload["categoryVisualGuidance"]["电竞音响"]
