from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cutme_intro import default_intro_plan_workspace
from .db import Database
from .md_parser import ParsedMarkdown, ScriptVariant, parse_markdown_file
from .markdown_paths import product_copy_library_path, project_asset_markdown_path
from .price_transition_plan import (
    find_price_transition_plan_for_text,
    load_price_transition_plan_set,
    price_transition_plan_path,
)
from .product_copy_audit import audit_parsed_product_copy
from .product_copy_lint import group_findings_by_uid, lint_parsed_product_copy
from .research_pack_service import ResearchPackService
from .repositories import Repository
from .subtitle_helpers import normalize_subtitle_alignment_text
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

    markdown_source = md_path.read_text(encoding="utf-8-sig") if parsed and md_path.is_file() else ""
    markdown_lint_findings = (
        lint_parsed_product_copy(parsed, source_text=markdown_source, source_path=md_path) if parsed else []
    )
    markdown_lint_by_uid = group_findings_by_uid(markdown_lint_findings)
    markdown_audit_findings = (
        audit_parsed_product_copy(parsed, source_text=markdown_source, source_path=md_path) if parsed else []
    )

    library_path = product_copy_library_path(project)
    library_parsed = parse_markdown_file(library_path) if library_path.is_file() else None
    library_source = library_path.read_text(encoding="utf-8-sig") if library_parsed else ""
    library_lint_findings = (
        lint_parsed_product_copy(library_parsed, source_text=library_source, source_path=library_path)
        if library_parsed
        else []
    )
    library_lint_by_uid = group_findings_by_uid(library_lint_findings)
    library_audit_findings = (
        audit_parsed_product_copy(library_parsed, source_text=library_source, source_path=library_path)
        if library_parsed
        else []
    )
    library_products = {product.uid: product for product in library_parsed.products} if library_parsed else {}

    intro_scripts = parsed.intro_scripts if parsed else []
    intro_plan_paths = {
        item.label: plan_path
        for item in intro_scripts
        if (plan_path := _matching_intro_plan(project_id, item)) is not None
    }
    eligible_intro_scripts = [item for item in intro_scripts if item.label in intro_plan_paths]
    selected_intro = _selected_intro(eligible_intro_scripts, intro_label)
    requested_intro = _selected_intro(intro_scripts, intro_label) if intro_label else None
    if not intro_scripts:
        issues.append(
            {
                "level": "warning",
                "code": "missing_intro_content",
                "message": "intro copy unit is missing; choose a current template and generate it from slots.",
            }
        )
    elif intro_label and requested_intro is None:
        issues.append(
            {
                "level": "error",
                "code": "intro_version_not_found",
                "message": f"selected intro version does not exist: {intro_label}",
                "available": [item.label for item in eligible_intro_scripts],
            }
        )
    elif intro_label and selected_intro is None:
        issues.append(
            {
                "level": "error",
                "code": "intro_template_required",
                "message": "selected Markdown intro is historical text without a matching template source plan.",
                "historical_labels": [requested_intro.label],
            }
        )
    elif not intro_label and not eligible_intro_scripts:
        issues.append(
            {
                "level": "error",
                "code": "intro_template_required",
                "message": "Markdown contains no template-generated intro with a matching source plan.",
                "historical_labels": [item.label for item in intro_scripts],
            }
        )
    elif not selected_intro and len(eligible_intro_scripts) > 1 and not intro_label:
        issues.append(
            {
                "level": "error",
                "code": "intro_version_not_selected",
                "message": "multiple template-generated intro versions exist; select one with --intro-label.",
                "available": [item.label for item in eligible_intro_scripts],
            }
        )

    source_intro_plan_path = ""
    if selected_intro:
        source_intro_plan_path = str(intro_plan_paths[selected_intro.label])

    md_products = {product.uid: product for product in parsed.products} if parsed else {}
    product_copy_ready = 0
    product_copy_library_ready = 0
    product_copy_lint_findings = 0
    product_copy_lint_failed_uids: set[str] = set()
    current_uids = {safe_text(product.get("uid")) for product in products}
    episode_copy_uids: set[str] = set()
    library_copy_uids: set[str] = set()
    for product in products:
        uid = safe_text(product.get("uid"))
        doc = md_products.get(uid)
        if doc and doc.scripts:
            episode_copy_uids.add(uid)
            lint_findings = markdown_lint_by_uid.get(uid, [])
            if lint_findings:
                product_copy_lint_findings += len(lint_findings)
                product_copy_lint_failed_uids.add(uid)
                issues.extend(_product_copy_lint_issues(lint_findings))
                continue
            product_copy_ready += 1
            continue
        library_doc = library_products.get(uid)
        if library_doc and library_doc.scripts:
            library_copy_uids.add(uid)
            lint_findings = library_lint_by_uid.get(uid, [])
            if lint_findings:
                product_copy_lint_findings += len(lint_findings)
                product_copy_lint_failed_uids.add(uid)
                issues.extend(_product_copy_lint_issues(lint_findings))
                continue
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

    selected_audit_findings = _selected_product_copy_audit_findings(
        markdown_audit_findings=markdown_audit_findings,
        library_audit_findings=library_audit_findings,
        current_uids=current_uids,
        episode_copy_uids=episode_copy_uids,
        library_copy_uids=library_copy_uids,
    )
    issues.extend(_product_copy_audit_issues(selected_audit_findings))

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
        active_product_uids={safe_text(product.get("uid")) for product in products},
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
            "intro_ready": len(eligible_intro_scripts),
            "intro_markdown_entries": len(intro_scripts),
            "product_copy_ready": product_copy_ready,
            "product_copy_library_ready": product_copy_library_ready,
            "product_copy_lint_failed_products": len(product_copy_lint_failed_uids),
            "product_copy_lint_findings": product_copy_lint_findings,
            "product_copy_style_findings": len(selected_audit_findings),
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


def _matching_intro_plan(project_id: int, intro: ScriptVariant) -> Path | None:
    safe_label = safe_text(intro.label)
    for char in '<>:"/\\|?*':
        safe_label = safe_label.replace(char, "_")
    safe_label = safe_label.strip(" .")
    if not safe_label:
        return None
    path = default_intro_plan_workspace(project_id) / f"source-intro-plan-{safe_label}.json"
    if not path.is_file():
        return None
    try:
        plan = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(plan, dict):
        return None
    planned_text = normalize_subtitle_alignment_text(safe_text(plan.get("full_script")))
    intro_text = normalize_subtitle_alignment_text(intro.body)
    return path if planned_text and planned_text == intro_text else None


def _markdown_sync_status(
    *,
    parsed: ParsedMarkdown | None,
    blocks: list[dict[str, Any]],
    selected_intro: ScriptVariant | None,
    products_total: int,
    active_product_uids: set[str],
) -> dict[str, Any]:
    if parsed is None:
        return {"synced": False, "synced_count": 0, "issues": []}

    expected: list[tuple[str, str, str, str, str]] = []
    if selected_intro:
        expected.append(("intro", "", "", selected_intro.label, selected_intro.body))
    for product in parsed.products:
        if product.uid not in active_product_uids:
            continue
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
            "intro_template_required",
            "intro_version_not_selected",
            "intro_version_not_found",
            "missing_matching_intro_plan",
            "invalid_price_transition_plan_set",
            "missing_price_transition_plan_set",
            "missing_matching_price_transition_plan",
            "missing_price_transition_copy",
            "missing_product_copy",
            "product_copy_lint_failed",
        }
    ):
        return "content_incomplete"
    if "markdown_not_synced" in codes or not synced:
        return "needs_sync"
    return "ready_for_downstream"


def _next_hint(db: Database, project_id: int, issues: list[dict[str, Any]], synced: bool) -> dict[str, Any]:
    codes = {safe_text(issue.get("code")) for issue in issues}
    research_pack_path = str(ResearchPackService(db).default_pack_path(project_id))
    if codes.intersection({"missing_intro_content", "intro_template_required"}):
        return {
            "action": "choose_intro_template",
            "task": "选择引言模板",
            "phase3_sequence_step": 1,
            "intro_plan_command": (
                f"python -m bworkflow_sql intro-plan {project_id} --template <template-id> "
                "--slots <slots.json> --label 引言1"
            ),
            "requires_user_template_selection": True,
            "requires_user_final_approval": False,
            "note": "先展示当前 CutMe 正式模板并等待用户选定；选定后生成结构化引言并重新运行 script-doctor。结构化引言完成前禁止写商品正文或价格过渡。",
        }
    if codes.intersection({"intro_version_not_selected", "intro_version_not_found"}):
        return {
            "action": "select_intro_version",
            "task": "选择引言版本",
            "phase3_sequence_step": 2,
            "command": f"python -m bworkflow_sql script-doctor {project_id} --intro-label 引言1",
            "requires_user_final_approval": False,
        }
    if "missing_matching_intro_plan" in codes:
        intro_issue = next(
            (issue for issue in issues if safe_text(issue.get("code")) == "missing_matching_intro_plan"),
            {},
        )
        command = safe_text(intro_issue.get("command")) or (
            f"python -m bworkflow_sql intro-plan {project_id} --template <template-id> "
            "--slots <slots.json> --label 引言1"
        )
        return {
            "action": "write_structured_intro",
            "task": "生成结构化引言",
            "phase3_sequence_step": 2,
            "command": command,
            "requires_user_final_approval": False,
            "note": "从已选择模板的 slots JSON 同源生成正式 Markdown 和 source-intro-plan；完成后重新运行 script-doctor。商品正文和价格过渡仍禁止抢跑。",
        }
    if "product_copy_lint_failed" in codes:
        return {
            "action": "fix_product_copy_language",
            "task": "修正商品口播中的内部标签和资料采集措辞",
            "command": f"python -m bworkflow_sql copy-lint {project_id}",
            "requires_user_final_approval": False,
            "note": "按 UID 和正文版本修正命中句，重新运行 copy-lint 与 script-doctor；通过前禁止同步、配音和组装。",
        }
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
    if codes.intersection({"missing_episode_markdown", "missing_product_copy", "missing_price_transition_copy"}):
        return {
            "action": "write_product_copy_and_price_transitions",
            "task": "写商品正文和结构化价格过渡",
            "phase3_sequence_step": 3,
            "command": f"python -m bworkflow_sql research-pack {project_id}",
            "outline_command": f"python -m bworkflow_sql outline {project_id}",
            "price_transition_plan_command": f"python -m bworkflow_sql price-transition-plan {project_id} --plan <price-transition-plan.json>",
            "research_pack_path": research_pack_path,
            "requires_user_final_approval": True,
            "note": "仅在结构化引言已就绪后执行。先联网补证据并写全部商品正文，再同源生成价格过渡正文与机器计划；两者完成后统一校验并等待用户定稿。",
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


def _product_copy_lint_issues(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "level": "error",
            "code": "product_copy_lint_failed",
            "uid": safe_text(finding.get("uid")),
            "title": safe_text(finding.get("title")),
            "block_label": safe_text(finding.get("block_label")),
            "rule_id": safe_text(finding.get("rule_id")),
            "category": safe_text(finding.get("category")),
            "match": safe_text(finding.get("match")),
            "message": safe_text(finding.get("message")),
            "suggestion": safe_text(finding.get("suggestion")),
            "path": safe_text(finding.get("path")),
            "line": int(finding.get("line") or 0),
            "column": int(finding.get("column") or 0),
            "excerpt": safe_text(finding.get("excerpt")),
        }
        for finding in findings
    ]


def _selected_product_copy_audit_findings(
    *,
    markdown_audit_findings: list[dict[str, Any]],
    library_audit_findings: list[dict[str, Any]],
    current_uids: set[str],
    episode_copy_uids: set[str],
    library_copy_uids: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    if episode_copy_uids:
        selected.extend(
            finding
            for finding in markdown_audit_findings
            if safe_text(finding.get("uid")) in current_uids
            or safe_text(finding.get("rule_id")) == "repeated_abstract_closing_form"
        )
    library_only_uids = (library_copy_uids - episode_copy_uids) & current_uids
    if library_only_uids:
        selected.extend(
            finding
            for finding in library_audit_findings
            if safe_text(finding.get("uid")) in library_only_uids
        )
    return selected


def _product_copy_audit_issues(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "level": "warning",
            "code": "product_copy_style_warning",
            "uid": safe_text(finding.get("uid")),
            "title": safe_text(finding.get("title")),
            "block_label": safe_text(finding.get("block_label")),
            "rule_id": safe_text(finding.get("rule_id")),
            "category": safe_text(finding.get("category")),
            "match": safe_text(finding.get("match")),
            "message": safe_text(finding.get("message")),
            "suggestion": safe_text(finding.get("suggestion")),
            "path": safe_text(finding.get("path")),
            "line": int(finding.get("line") or 0),
            "column": int(finding.get("column") or 0),
            "excerpt": safe_text(finding.get("excerpt")),
            "locations": finding.get("locations", []),
            "blocking": False,
        }
        for finding in findings
    ]
