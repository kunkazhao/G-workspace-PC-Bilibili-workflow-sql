from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


RECIPE_SCHEMA_VERSION = "1.0.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_production_recipe(
    *,
    job_package_path: str | Path,
    source_package_path: str | Path,
    cutme_result: dict[str, Any],
) -> dict[str, Any]:
    job_path = Path(job_package_path).expanduser().resolve()
    source_path = Path(source_package_path).expanduser().resolve()
    package = json.loads(job_path.read_text(encoding="utf-8-sig"))
    frozen_package = _absolutize_existing_paths(package, base=job_path.parent)
    sources = _collect_file_sources(frozen_package)
    renderer_source = Path(
        str((cutme_result.get("artifacts") or {}).get("renderer_source_path") or "")
    ).expanduser()
    if renderer_source.is_file():
        sources.append(_source_fingerprint("cutme_renderer_source", renderer_source))
    sources = _deduplicate_sources(sources)
    recipe: dict[str, Any] = {
        "schemaVersion": RECIPE_SCHEMA_VERSION,
        "kind": "bworkflow.production_recipe",
        "renderPackage": frozen_package,
        "sourceFiles": sources,
        "provenancePackages": [
            _source_fingerprint("cutme_job_package", job_path),
            _source_fingerprint("source_render_package", source_path),
        ],
        "renderer": {
            "adapter_contract": "cutme.render_result/1.0.0",
            "result_schema_version": cutme_result.get("schema_version"),
            "operation": cutme_result.get("operation"),
            "renderer_version": (cutme_result.get("artifacts") or {}).get("renderer_version"),
        },
    }
    recipe["recipeSha256"] = canonical_sha256(recipe)
    return recipe


def write_production_recipe(recipe: dict[str, Any], path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def validate_production_recipe(path: str | Path, *, expected_sha256: str = "") -> dict[str, Any]:
    recipe_path = Path(path).expanduser().resolve()
    blocked_by: list[dict[str, Any]] = []
    if not recipe_path.is_file():
        return {
            "ok": False,
            "rerenderable": False,
            "recipe_status": "sources_missing",
            "blocked_by": [{"code": "recipe_missing", "path": str(recipe_path)}],
        }
    recipe = json.loads(recipe_path.read_text(encoding="utf-8-sig"))
    if recipe.get("kind") != "bworkflow.production_recipe" or recipe.get("schemaVersion") != RECIPE_SCHEMA_VERSION:
        blocked_by.append({"code": "recipe_contract_invalid", "path": str(recipe_path)})
    stored_recipe_hash = str(recipe.get("recipeSha256") or "")
    hash_payload = dict(recipe)
    hash_payload.pop("recipeSha256", None)
    actual_recipe_hash = canonical_sha256(hash_payload)
    if stored_recipe_hash != actual_recipe_hash or (expected_sha256 and expected_sha256 != actual_recipe_hash):
        blocked_by.append({"code": "recipe_hash_mismatch", "path": str(recipe_path)})
    for source in recipe.get("sourceFiles") or []:
        if not isinstance(source, dict):
            continue
        source_path = Path(str(source.get("path") or "")).expanduser()
        if not source_path.is_file():
            blocked_by.append({"code": "source_missing", "role": source.get("role"), "path": str(source_path)})
            continue
        if int(source.get("size") or 0) != source_path.stat().st_size or str(source.get("sha256") or "") != sha256_file(source_path):
            blocked_by.append({"code": "source_changed", "role": source.get("role"), "path": str(source_path)})
    if any(
        item["code"] in {"recipe_contract_invalid", "recipe_hash_mismatch"}
        or (
            item.get("role") == "cutme_renderer_source"
            and item["code"] in {"source_missing", "source_changed"}
        )
        for item in blocked_by
    ):
        status = "version_drift"
    elif blocked_by:
        status = "sources_missing"
    else:
        status = "reproducible"
    return {
        "ok": not blocked_by,
        "rerenderable": not blocked_by,
        "recipe_status": status,
        "recipe_path": str(recipe_path),
        "recipe_sha256": actual_recipe_hash,
        "source_count": len(recipe.get("sourceFiles") or []),
        "blocked_by": blocked_by,
        "recipe": recipe,
    }


def _absolutize_existing_paths(value: Any, *, base: Path) -> Any:
    if isinstance(value, dict):
        return {key: _absolutize_existing_paths(item, base=base) for key, item in value.items()}
    if isinstance(value, list):
        return [_absolutize_existing_paths(item, base=base) for item in value]
    if not isinstance(value, str) or not value.strip():
        return value
    candidate = Path(value).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    return str(resolved) if resolved.is_file() else value


def _collect_file_sources(value: Any, *, trail: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            sources.extend(_collect_file_sources(item, trail=(*trail, str(key))))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            sources.extend(_collect_file_sources(item, trail=(*trail, str(index))))
    elif isinstance(value, str):
        path = Path(value).expanduser()
        if path.is_absolute() and path.is_file():
            sources.append(_source_fingerprint("renderPackage." + ".".join(trail), path))
    return sources


def _source_fingerprint(role: str, path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {"role": role, "path": str(resolved), "size": resolved.stat().st_size, "sha256": sha256_file(resolved)}


def _deduplicate_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for source in sources:
        unique.setdefault(str(source["path"]).casefold(), source)
    return sorted(unique.values(), key=lambda item: (str(item["role"]), str(item["path"])))
