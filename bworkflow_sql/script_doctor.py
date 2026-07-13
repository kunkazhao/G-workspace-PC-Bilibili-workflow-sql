from __future__ import annotations

from pathlib import Path
from typing import Any

from .cutme_intro import find_intro_plan_for_text
from .db import Database
from .md_parser import ParsedMarkdown, ScriptVariant, parse_markdown_file
from .markdown_paths import product_copy_library_path, project_asset_markdown_path
from .price_transition_plan import (
    find_price_transition_plan_for_text,
    load_price_transition_plan_set,
    price_transition_plan_path,
)
from .research_pack_service import ResearchPackService
from .repositories import Repository
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
    bound_md_path = Path(safe_text(project.get("md_path")))
    md_path, md_path_issue_code = project_asset_markdown_path(project)
    parsed: ParsedMarkdown | None = None
    if md_path_issue_code:
        issues.append(
            {
                "level": "warning",
                "code": md_path_issue_code,
                "message": "project md_path is not the reusable product-copy asset Markdown; using the product-copy library path instead.",
                "bound_path": str(bound_md_path) if bound_md_path else "",
                "asset_path": str(md_path),
            }
        )

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

    library_path = product_copy_library_path(project)
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
                    "command": f"python -m bworkflow_sql intro-plan {project_id} --slots <slots.json> --label {selected_intro.label}",
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
    price_transition_ready = sum(len(item.scripts) for item in price_transitions)
    library_price_transition_ready = (
        sum(len(item.scripts) for item in library_parsed.price_transitions) if library_parsed else 0
    )
    if parsed and price_transitions and not price_transition_ready:
        issues.append(
            {
                "level": "info",
                "code": "missing_price_transition_copy",
                "message": "current episode Markdown has price transition headings but no ready price transition body.",
            }
        )
    elif not price_transition_ready and library_price_transition_ready:
        issues.append(
            {
                "level": "info",
                "code": "episode_price_transition_needs_materialization",
                "message": "reusable price transition copy exists in the product-copy library but is not materialized into the episode Markdown.",
                "source_path": str(library_path),
                "count": library_price_transition_ready,
            }
        )
    elif parsed and not price_transition_ready:
        issues.append(
            {
                "level": "info",
                "code": "missing_price_transition_copy",
                "message": "current episode Markdown has no price transition copy; render can skip transitions, but phase-3 copy may be incomplete.",
            }
        )

    source_price_transition_plan_path = ""
    try:
        price_plan_set = load_price_transition_plan_set(project_id)
    except ValueError as exc:
        price_plan_set = None
        source_price_transition_plan_path = str(price_transition_plan_path(project_id))
        issues.append(
            {
                "level": "error",
                "code": "invalid_price_transition_plan_set",
                "message": str(exc),
                "path": source_price_transition_plan_path,
            }
        )
    if price_plan_set is not None:
        source_price_transition_plan_path = str(price_transition_plan_path(project_id))
        for price in price_transitions:
            for script in price.scripts:
                matched = find_price_transition_plan_for_text(
                    project_id,
                    price_range_label=price.label,
                    block_label=script.label,
                    body=script.body,
                )
                if matched is None:
                    issues.append(
                        {
                            "level": "error",
                            "code": "missing_matching_price_transition_plan",
                            "message": "价格过渡正文与结构化自动剪辑计划不匹配；必须重建计划，不能退回关键词猜测。",
                            "price_range_label": price.label,
                            "block_label": script.label,
                        }
                    )
    elif price_transition_ready and not source_price_transition_plan_path:
        issues.append(
            {
                "level": "error",
                "code": "missing_price_transition_plan_set",
                "message": "价格过渡正文缺少结构化自动剪辑计划；必须生成计划，不能依赖通用关键词猜测。",
                "command": f"python -m bworkflow_sql price-transition-plan {project_id} --plan <price-transition-plan.json>",
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
            "bound_md_path": str(bound_md_path) if bound_md_path else "",
        },
        "summary": {
            "products_total": len(products),
            "intro_ready": len(intro_scripts),
            "product_copy_ready": product_copy_ready,
            "product_copy_library_ready": product_copy_library_ready,
            "price_transition_sections": len(price_transitions),
            "price_transition_ready": price_transition_ready,
            "price_transition_library_ready": library_price_transition_ready,
            "price_transition_plan_ready": bool(price_plan_set),
            "script_blocks_synced": markdown_sync["synced_count"],
        },
        "selected_intro": {
            "label": selected_intro.label if selected_intro else "",
            "source_intro_plan_path": source_intro_plan_path,
        },
        "price_transition_plan": {
            "source_path": source_price_transition_plan_path,
            "strict": bool(source_price_transition_plan_path),
        },
        "issues": issues,
        "next": _next_hint(db, project_id, issues, markdown_sync["synced"]),
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
            "invalid_price_transition_plan_set",
            "missing_price_transition_plan_set",
            "missing_matching_price_transition_plan",
            "missing_product_copy",
        }
    ):
        return "content_incomplete"
    if "markdown_not_synced" in codes or not synced:
        return "needs_sync"
    return "ready_for_downstream"


def _next_hint(db: Database, project_id: int, issues: list[dict[str, Any]], synced: bool) -> dict[str, Any]:
    codes = {safe_text(issue.get("code")) for issue in issues}
    research_pack_path = str(ResearchPackService(db).default_pack_path(project_id))
    if "episode_markdown_needs_materialization" in codes and "missing_product_copy" not in codes:
        materialize_issue = next(
            (issue for issue in issues if safe_text(issue.get("code")) == "episode_markdown_needs_materialization"),
            {},
        )
        return {
            "action": "materialize_episode_markdown",
            "task": "把已写好的单品文案放入口播草稿",
            "command": f"python -m bworkflow_sql materialize-episode {project_id}",
            "source_path": safe_text(materialize_issue.get("source_path")),
            "requires_user_final_approval": True,
        }
    if "intro_version_not_selected" in codes:
        return {
            "action": "select_intro_version",
            "task": "选择引言版本",
            "command": f"python -m bworkflow_sql script-doctor {project_id} --intro-label 引言1",
            "requires_user_final_approval": False,
        }
    if "missing_matching_intro_plan" in codes:
        intro_issue = next(
            (issue for issue in issues if safe_text(issue.get("code")) == "missing_matching_intro_plan"),
            {},
        )
        command = safe_text(intro_issue.get("command")) or (
            f"python -m bworkflow_sql intro-plan {project_id} --slots <slots.json> --label 引言1"
        )
        return {
            "action": "create_intro_plan",
            "task": "补引言剪辑计划",
            "command": command,
            "requires_user_final_approval": False,
            "note": "先从 slots JSON 同源生成正式 Markdown 和 source-intro-plan；用户定稿前不要加 --sync，也不要配音和组装。",
        }
    if codes.intersection(
        {
            "invalid_price_transition_plan_set",
            "missing_price_transition_plan_set",
            "missing_matching_price_transition_plan",
        }
    ):
        return {
            "action": "rebuild_price_transition_plan",
            "task": "重建价格过渡自动剪辑计划",
            "command": f"python -m bworkflow_sql price-transition-plan {project_id} --plan <price-transition-plan.json>",
            "requires_user_final_approval": False,
            "note": "命令同源更新正式 Markdown 和机器计划；定稿前不要加 --sync。",
        }
    if codes.intersection(
        {
            "missing_episode_markdown",
            "missing_intro_content",
            "intro_version_not_found",
            "missing_product_copy",
        }
    ):
        return {
            "action": "fill_content_units",
            "task": "写文案草稿",
            "command": f"python -m bworkflow_sql research-pack {project_id}",
            "outline_command": f"python -m bworkflow_sql outline {project_id}",
            "intro_plan_command": f"python -m bworkflow_sql intro-plan {project_id} --slots <slots.json> --label 引言1",
            "price_transition_plan_command": f"python -m bworkflow_sql price-transition-plan {project_id} --plan <price-transition-plan.json>",
            "research_pack_path": research_pack_path,
            "requires_user_final_approval": True,
            "note": "先建/补资料采集包并联网填证据，再写单品文案；引言和价格过渡分别通过结构化命令同源写入正式 Markdown 与机器计划。用户定稿前不入库、不配音、不组口播稿。",
        }
    if "markdown_not_synced" in codes or not synced:
        return {
            "action": "sync_markdown",
            "task": "定稿后同步入库",
            "command": f"python -m bworkflow_sql sync {project_id} --step markdown",
            "requires_user_final_approval": True,
            "note": "只有用户明确确认定稿后才执行；同步后文案 hash 固定，后续配音按当前 hash 匹配。",
        }
    return {
        "action": "continue_downstream",
        "task": "进入配音检查",
        "command": f"python -m bworkflow_sql voice-counts {project_id} --account <account>",
        "requires_user_final_approval": False,
        "note": "文案已入库后先检查配音，不直接组口播稿。",
    }


def _product_copy_library_path(project: dict[str, Any]) -> Path:
    return product_copy_library_path(project)
