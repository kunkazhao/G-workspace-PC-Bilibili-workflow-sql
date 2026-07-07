from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import Database
from .markdown_paths import project_asset_markdown_path
from .md_parser import ParsedMarkdown, ProductDoc, ScriptVariant, parse_markdown_file
from .outline_service import format_product_heading, render_price_transitions, render_product_body
from .repositories import Repository
from .script_doctor import _product_copy_library_path
from .utils import safe_text


def materialize_episode_markdown(
    db: Database,
    *,
    project_id: int,
    library_path: str | Path | None = None,
    episode_path: str | Path | None = None,
) -> dict[str, Any]:
    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")
    products = repo.products(project_id, include_removed=False)
    if not products:
        raise ValueError(f"project has no active products: {project_id}")

    source_path = Path(library_path) if library_path else _product_copy_library_path(project)
    if not source_path.is_file():
        raise FileNotFoundError(f"product copy library does not exist: {source_path}")
    target_path = Path(episode_path) if episode_path else project_asset_markdown_path(project)[0]
    if not safe_text(str(target_path)):
        raise ValueError("project has no episode Markdown path")

    library = parse_markdown_file(source_path)
    existing = parse_markdown_file(target_path) if target_path.is_file() else None
    library_products = {item.uid: item for item in library.products}
    existing_products = {item.uid: item for item in existing.products} if existing else {}
    source_intro_scripts = _source_intro_scripts(project_id)
    active_uids = {safe_text(item.get("uid")) for item in products}
    missing_library_copy: list[dict[str, str]] = []
    materialized = 0

    lines: list[str] = []
    lines += ["## 引言文案", ""]
    intro_scripts = _materialized_intro_scripts(existing, source_intro_scripts)
    if intro_scripts:
        for intro in intro_scripts:
            lines += [f"### {intro.label}", intro.body.strip(), ""]
    else:
        lines += ["### 引言1", "", ""]

    lines += ["## 商品文案", ""]
    for product in products:
        uid = safe_text(product.get("uid"))
        doc = _materialized_product_doc(existing_products.get(uid), library_products.get(uid))
        lines += [f"### {format_product_heading(product)}", ""]
        if doc and doc.scripts:
            materialized += 1
            lines.extend(render_product_body(doc))
        else:
            missing_library_copy.append({"uid": uid, "title": safe_text(product.get("title"))})
            lines += ["#### 正文", ""]
        lines.append("")

    removed = [item for uid, item in existing_products.items() if uid not in active_uids] if existing else []
    if removed:
        lines += ["## 已移出 Master 的商品文案", ""]
        for item in removed:
            lines += [f"### {item.price_label or '未定价'}-{item.uid}-{item.title}", ""]
            lines.extend(render_product_body(item))
            lines.append("")

    price_transitions = existing.price_transitions if existing and existing.price_transitions else library.price_transitions
    lines += render_price_transitions(price_transitions, None)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    db.execute("UPDATE projects SET md_path=?, updated_at=datetime('now') WHERE id=?", (str(target_path), project_id))
    return {
        "ok": not missing_library_copy,
        "project_id": int(project_id),
        "source_path": str(source_path),
        "target_path": str(target_path),
        "materialized": materialized,
        "price_transitions_materialized": len(price_transitions),
        "missing_library_copy": missing_library_copy,
        "total": len(products),
    }


def _materialized_product_doc(existing: ProductDoc | None, library: ProductDoc | None) -> ProductDoc | None:
    if existing and existing.scripts:
        return existing
    if library and library.scripts:
        return library
    return existing or library


def _materialized_intro_scripts(
    existing: ParsedMarkdown | None,
    source_intro_scripts: dict[str, ScriptVariant],
) -> list[ScriptVariant]:
    existing_scripts = list(existing.intro_scripts if existing else [])
    if not source_intro_scripts:
        return existing_scripts

    result: list[ScriptVariant] = []
    seen: set[str] = set()
    for script in existing_scripts:
        label = safe_text(script.label)
        source_script = source_intro_scripts.get(label)
        if source_script:
            result.append(source_script)
            seen.add(label)
            continue
        if label == "正文":
            continue
        result.append(script)
        seen.add(label)
    for label, script in source_intro_scripts.items():
        if label not in seen:
            result.append(script)
    return result


def _source_intro_scripts(project_id: int) -> dict[str, ScriptVariant]:
    from .cutme_intro import ALLOWED_INTRO_TEMPLATE_IDS, default_intro_plan_workspace

    workspace = default_intro_plan_workspace(project_id)
    if not workspace.is_dir():
        return {}
    result: dict[str, ScriptVariant] = {}
    for path in sorted(workspace.glob("source-intro-plan-*.json")):
        label = path.stem.removeprefix("source-intro-plan-")
        if not label:
            continue
        try:
            plan = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(plan, dict):
            continue
        template_id = safe_text(plan.get("template_id") or plan.get("templateId"))
        if template_id not in ALLOWED_INTRO_TEMPLATE_IDS:
            continue
        full_script = safe_text(plan.get("full_script"))
        if not full_script:
            continue
        result[label] = ScriptVariant(label=label, body=full_script, script_id="")
    return result
