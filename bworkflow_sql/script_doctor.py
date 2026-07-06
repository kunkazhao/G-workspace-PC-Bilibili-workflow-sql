from __future__ import annotations

from pathlib import Path
from typing import Any

from .cutme_intro import find_intro_plan_for_text
from .db import Database
from .md_parser import ParsedMarkdown, ScriptVariant, parse_markdown_file
from .repositories import Repository
from .settings import DEFAULT_MARKDOWN_ROOT
from .utils import safe_text, text_hash


def diagnose_script_flow(
    db: Database,
    *,
    project_id: int,
    intro_label: str = "",
) -> dict[str, Any]:
    repo = Repository(db)
    project = repo.project(project_id)
    if not project:
        raise ValueError(f"project does not exist: {project_id}")

    products = repo.products(project_id, include_removed=False)
    blocks = repo.script_blocks(project_id)
    issues: list[dict[str, Any]] = []
    md_path = Path(safe_text(project.get("md_path")))
    parsed: ParsedMarkdown | None = None

    if not md_path or not md_path.is_file():
        issues.append(
            {
                "level": "error",
                "code": "missing_episode_markdown",
                "message": "current project has no readable episode Markdown.",
                "path": str(md_path) if md_path else "",
            }
        )
    else:
        parsed = parse_markdown_file(md_path)

    library_path = _product_copy_library_path(project)
    library_parsed = parse_markdown_file(library_path) if library_path.is_file() else None
    library_products = {product.uid: product for product in library_parsed.products} if library_parsed else {}

    intro_scripts = parsed.intro_scripts if parsed else []
    selected_intro = _selected_intro(intro_scripts, intro_label)
    if not intro_scripts:
        issues.append(
            {
                "level": "warning",
                "code": "missing_intro_content",
                "message": "intro copy unit is missing; fill or select an intro version before intro video production.",
            }
        )
    elif not selected_intro and len(intro_scripts) > 1 and not intro_label:
        issues.append(
            {
                "level": "error",
                "code": "intro_version_not_selected",
                "message": "multiple intro versions exist; select one with --intro-label before downstream production.",
                "available": [item.label for item in intro_scripts],
            }
        )
    elif not selected_intro and intro_label:
        issues.append(
            {
                "level": "error",
                "code": "intro_version_not_found",
                "message": f"selected intro version does not exist: {intro_label}",
                "available": [item.label for item in intro_scripts],
            }
        )

    source_intro_plan_path = ""
    if selected_intro:
        matched_plan = find_intro_plan_for_text(project_id, selected_intro.body)
        if matched_plan:
            source_intro_plan_path = str(matched_plan)
        else:
            issues.append(
                {
                    "level": "warning",
                    "code": "missing_matching_intro_plan",
                    "message": "selected intro text has no matching source-intro-plan JSON; run intro-plan from slots or select a matching intro.",
                    "intro_label": selected_intro.label,
                    "command": f"python -m bworkflow_sql intro-plan {project_id} --slots <slots.json> --label {selected_intro.label} --sync",
                }
            )

    md_products = {product.uid: product for product in parsed.products} if parsed else {}
    product_copy_ready = 0
    product_copy_library_ready = 0
    for product in products:
        uid = safe_text(product.get("uid"))
        doc = md_products.get(uid)
        if doc and doc.scripts:
            product_copy_ready += 1
            continue
        library_doc = library_products.get(uid)
        if library_doc and library_doc.scripts:
            product_copy_library_ready += 1
            continue
        issues.append(
            {
                "level": "warning",
                "code": "missing_product_copy",
                "uid": uid,
                "title": safe_text(product.get("title")),
                "message": "scheme product has no reusable product copy unit in episode Markdown.",
            }
        )

    if product_copy_library_ready and product_copy_ready < len(products):
        issues.append(
            {
                "level": "info",
                "code": "episode_markdown_needs_materialization",
                "message": "reusable product copy exists in the product-copy library but is not materialized into the episode Markdown.",
                "source_path": str(library_path),
                "count": product_copy_library_ready,
            }
        )

    extra_product_uids = sorted(uid for uid in md_products if uid not in {safe_text(p.get("uid")) for p in products})
    for uid in extra_product_uids:
        issues.append(
            {
                "level": "warning",
                "code": "extra_markdown_product",
                "uid": uid,
                "title": md_products[uid].title,
                "message": "Markdown contains product copy that is not in the current scheme.",
            }
        )

    price_transitions = parsed.price_transitions if parsed else []
    if parsed and not price_transitions:
        issues.append(
            {
                "level": "info",
                "code": "missing_price_transition_copy",
                "message": "price transition copy is absent; render can skip transitions, but phase-3 copy may be incomplete.",
            }
        )

    markdown_sync = _markdown_sync_status(
        parsed=parsed,
        blocks=blocks,
        selected_intro=selected_intro,
        products_total=len(products),
    )
    issues.extend(markdown_sync["issues"])

    status = _status(issues, markdown_sync["synced"])
    return {
        "ok": status == "ready_for_downstream",
        "status": status,
        "project": {
            "id": int(project_id),
            "name": safe_text(project.get("name")),
            "scheme_id": safe_text(project.get("scheme_id")),
            "scheme_name": safe_text(project.get("scheme_name")),
            "md_path": str(md_path) if md_path else "",
        },
        "summary": {
            "products_total": len(products),
            "intro_ready": len(intro_scripts),
            "product_copy_ready": product_copy_ready,
            "product_copy_library_ready": product_copy_library_ready,
            "price_transition_ready": sum(len(item.scripts) for item in price_transitions),
            "script_blocks_synced": markdown_sync["synced_count"],
        },
        "selected_intro": {
            "label": selected_intro.label if selected_intro else "",
            "source_intro_plan_path": source_intro_plan_path,
        },
        "issues": issues,
        "next": _next_hint(project_id, issues, markdown_sync["synced"]),
    }


def _selected_intro(intro_scripts: list[ScriptVariant], intro_label: str) -> ScriptVariant | None:
    label = safe_text(intro_label)
    if label:
        return next((item for item in intro_scripts if item.label == label), None)
    if len(intro_scripts) == 1:
        return intro_scripts[0]
    return None


def _markdown_sync_status(
    *,
    parsed: ParsedMarkdown | None,
    blocks: list[dict[str, Any]],
    selected_intro: ScriptVariant | None,
    products_total: int,
) -> dict[str, Any]:
    if parsed is None:
        return {"synced": False, "synced_count": 0, "issues": []}

    expected: list[tuple[str, str, str, str, str]] = []
    if selected_intro:
        expected.append(("intro", "", "", selected_intro.label, selected_intro.body))
    elif len(parsed.intro_scripts) == 1:
        item = parsed.intro_scripts[0]
        expected.append(("intro", "", "", item.label, item.body))
    for product in parsed.products:
        for script in product.scripts:
            expected.append(("product", product.uid, "", script.label or "正文", script.body))
    for price in parsed.price_transitions:
        for script in price.scripts:
            expected.append(("price_transition", "", price.label, script.label or "正文", script.body))

    if not expected:
        return {"synced": False, "synced_count": 0, "issues": []}

    block_map = {
        (
            safe_text(block.get("script_type")),
            safe_text(block.get("owner_uid")),
            safe_text(block.get("price_range_label")),
            safe_text(block.get("block_label")),
        ): block
        for block in blocks
    }
    missing_or_stale = []
    for script_type, owner_uid, price_label, block_label, body in expected:
        block = block_map.get((script_type, owner_uid, price_label, block_label))
        if not block or safe_text(block.get("text_hash")) != text_hash(body):
            missing_or_stale.append(
                {
                    "script_type": script_type,
                    "owner_uid": owner_uid,
                    "price_range_label": price_label,
                    "block_label": block_label,
                }
            )

    has_content_ready = any(item[0] == "intro" for item in expected) and (
        sum(1 for item in expected if item[0] == "product") >= products_total
    )
    if has_content_ready and missing_or_stale:
        return {
            "synced": False,
            "synced_count": len(blocks),
            "issues": [
                {
                    "level": "warning",
                    "code": "markdown_not_synced",
                    "message": "episode Markdown has copy units that are not synced to script_blocks.",
                    "count": len(missing_or_stale),
                }
            ],
        }
    return {"synced": has_content_ready, "synced_count": len(blocks), "issues": []}


def _status(issues: list[dict[str, Any]], synced: bool) -> str:
    codes = {safe_text(issue.get("code")) for issue in issues}
    if codes.intersection(
        {
            "missing_episode_markdown",
            "missing_intro_content",
            "intro_version_not_selected",
            "intro_version_not_found",
            "missing_matching_intro_plan",
            "missing_product_copy",
        }
    ):
        return "content_incomplete"
    if "markdown_not_synced" in codes or not synced:
        return "needs_sync"
    return "ready_for_downstream"


def _next_hint(project_id: int, issues: list[dict[str, Any]], synced: bool) -> dict[str, Any]:
    codes = {safe_text(issue.get("code")) for issue in issues}
    if "episode_markdown_needs_materialization" in codes and "missing_product_copy" not in codes:
        materialize_issue = next(
            (issue for issue in issues if safe_text(issue.get("code")) == "episode_markdown_needs_materialization"),
            {},
        )
        return {
            "action": "materialize_episode_markdown",
            "command": f"python -m bworkflow_sql materialize-episode {project_id}",
            "source_path": safe_text(materialize_issue.get("source_path")),
        }
    if "intro_version_not_selected" in codes:
        return {
            "action": "select_intro_version",
            "command": f"python -m bworkflow_sql script-doctor {project_id} --intro-label 引言1",
        }
    if codes.intersection(
        {
            "missing_episode_markdown",
            "missing_intro_content",
            "intro_version_not_found",
            "missing_matching_intro_plan",
            "missing_product_copy",
        }
    ):
        return {
            "action": "fill_content_units",
            "command": f"python -m bworkflow_sql outline {project_id}",
        }
    if "markdown_not_synced" in codes or not synced:
        return {
            "action": "sync_markdown",
            "command": f"python -m bworkflow_sql sync {project_id} --step markdown",
        }
    return {
        "action": "continue_downstream",
        "command": f"python -m bworkflow_sql voice-counts {project_id} --account <account>",
    }


def _product_copy_library_path(project: dict[str, Any]) -> Path:
    parent = safe_text(project.get("category_parent_name"))
    child = safe_text(project.get("category_name"))
    if parent and child:
        return DEFAULT_MARKDOWN_ROOT / f"{parent}-{child}.md"
    name = safe_text(project.get("name"))
    return DEFAULT_MARKDOWN_ROOT / f"{name}.md"
