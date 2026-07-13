import json
from pathlib import Path

from bworkflow_sql.production_recipe import (
    build_production_recipe,
    validate_production_recipe,
    write_production_recipe,
)


def test_recipe_fingerprints_nested_render_assets_and_detects_source_change(tmp_path: Path):
    source_package = tmp_path / "source-package.json"
    source_package.write_text("{}", encoding="utf-8")
    job_root = tmp_path / "job"
    asset = job_root / "assets" / "voice.mp3"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"voice-v1")
    job_package = job_root / "render-package.json"
    job_package.write_text(
        json.dumps({"schemaVersion": "1.0.0", "segments": [{"voiceAsset": "assets/voice.mp3"}]}),
        encoding="utf-8",
    )
    recipe = build_production_recipe(
        job_package_path=job_package,
        source_package_path=source_package,
        cutme_result={"schema_version": "1.0.0", "operation": "render_final"},
    )
    recipe_path = write_production_recipe(recipe, tmp_path / "recipe.json")

    ready = validate_production_recipe(recipe_path, expected_sha256=recipe["recipeSha256"])
    assert ready["rerenderable"] is True
    assert str(asset.resolve()) in [item["path"] for item in recipe["sourceFiles"]]

    asset.write_bytes(b"voice-v2")
    changed = validate_production_recipe(recipe_path, expected_sha256=recipe["recipeSha256"])
    assert changed["rerenderable"] is False
    assert any(item["code"] == "source_changed" for item in changed["blocked_by"])


def test_recipe_detects_contract_tampering(tmp_path: Path):
    source_package = tmp_path / "source-package.json"
    job_package = tmp_path / "job-package.json"
    source_package.write_text("{}", encoding="utf-8")
    job_package.write_text(json.dumps({"segments": []}), encoding="utf-8")
    recipe = build_production_recipe(
        job_package_path=job_package,
        source_package_path=source_package,
        cutme_result={"schema_version": "1.0.0", "operation": "render_final"},
    )
    recipe_path = write_production_recipe(recipe, tmp_path / "recipe.json")
    payload = json.loads(recipe_path.read_text(encoding="utf-8"))
    payload["renderPackage"]["segments"].append({"type": "unexpected"})
    recipe_path.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_production_recipe(recipe_path, expected_sha256=recipe["recipeSha256"])
    assert result["rerenderable"] is False
    assert result["recipe_status"] == "version_drift"
