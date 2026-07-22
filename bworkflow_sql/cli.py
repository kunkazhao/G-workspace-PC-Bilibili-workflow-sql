"""B-Workflow SQL Headless CLI — 供外部工具（Claude Skill 等）通过 subprocess 调用。

用法:
  python -m bworkflow_sql projects
  python -m bworkflow_sql status 3
  python -m bworkflow_sql sync 3
  python -m bworkflow_sql voice 3 --account 小博
  python -m bworkflow_sql assemble 3 --account 小博 --intro-index 1
  python -m bworkflow_sql jianying 3 --manifest manifest.json --draft-name 充电宝
  python -m bworkflow_sql assets-check 3
  python -m bworkflow_sql voice-counts 3 --account 小博
  python -m bworkflow_sql product-images 3 --account 小博 --mode stale
  python -m bworkflow_sql product-images 3 --account 小博 --mode stale --product-uid P001
  python -m bworkflow_sql product-images 3 --account 小博 --mode missing
  python -m bworkflow_sql template-calibrate 3 --account 小燃 --product-uid R001
  python -m bworkflow_sql copy-lint 3
  python -m bworkflow_sql copy-audit 3 --voice-profile zhaoer
  python -m bworkflow_sql render-final-video 3 --account 小燃 --product-media-mode video_preferred

所有命令输出 JSON 到 stdout，错误输出 JSON 到 stderr。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any

from .cutme_intro import preflight_intro_plan_for_cutme
from .cutme_adapter import CutMeAdapterError
from .public_contracts import build_workflow_observation, build_workflow_observation_error
from .phase7_selection import (
    Phase7SelectionError,
    confirm_phase7_selection,
    validated_phase7_selection,
)
from .settings import DEFAULT_INTRO_ASSET_ROOT, DEFAULT_MASTER_API_BASE_URL
from .tts_helpers import VOICE_PROVIDER_INDEXTTS, VOICE_PROVIDER_MINIMAX
from .workflow_errors import (
    AmbiguousProjectReferenceError,
    InvalidWorkflowRequestError,
    ProjectNotFoundError,
)
from .template_calibration_runner import (
    load_template_calibration_targets,
    run_template_calibration_targets,
)

def _json_out(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _json_err(message: str, code: int = 1) -> None:
    print(
        json.dumps({"ok": False, "error": message}, ensure_ascii=False),
        file=sys.stderr,
    )
    sys.exit(code)


def _init() -> tuple:
    from .db import Database
    from .repositories import Repository
    from .sync_service import SyncService
    from .workflow_service import WorkflowService

    db = Database()
    repo = Repository(db)
    sync = SyncService(db)
    wf = WorkflowService(db)
    return db, repo, sync, wf


# ── projects ──────────────────────────────────────────────────────────

def cmd_projects(_args: argparse.Namespace) -> None:
    _, repo, _, _ = _init()
    projects = repo.projects()
    _json_out({
        "ok": True,
        "count": len(projects),
        "projects": [
            {
                "id": p["id"],
                "name": p["name"],
                "category": p.get("category", ""),
                "workspace_id": p.get("workspace_id", ""),
                "scheme_id": p.get("scheme_id", ""),
                "scheme_name": p.get("scheme_name", ""),
                "updated_at": p.get("updated_at", ""),
            }
            for p in projects
        ],
    })


# ── create-project ───────────────────────────────────────────────────

def cmd_create_project(args: argparse.Namespace) -> None:
    from .settings import (
        DEFAULT_IMAGE_ROOT,
        DEFAULT_SPOKEN_MD_ROOT,
        DEFAULT_VIDEO_ROOT,
        DEFAULT_VOICE_ROOT,
        INTERNAL_WORKSPACE_ROOT,
    )
    from .ui_helpers import DEFAULT_SPOKEN_MONTH_PREFIX
    from .utils import now_iso

    db, repo, sync, _ = _init()
    existing = db.fetchone(
        "SELECT id FROM projects WHERE workspace_id=? AND category_id=? AND scheme_id=? ORDER BY id DESC LIMIT 1",
        (args.workspace_id, args.category_id, args.scheme_id),
    )
    md_path = Path(args.md_path) if args.md_path else DEFAULT_SPOKEN_MD_ROOT / args.name / f"{DEFAULT_SPOKEN_MONTH_PREFIX}-小博.md"
    project_id = db.upsert_project(
        {
            "id": int(existing["id"]) if existing else 0,
            "name": args.name,
            "workspace_id": args.workspace_id,
            "workspace_name": args.workspace_name,
            "category_parent_id": args.category_parent_id or "",
            "category_parent_name": args.category_parent_name or "",
            "category_id": args.category_id,
            "category_name": args.category_name,
            "scheme_id": args.scheme_id,
            "scheme_name": args.scheme_name,
            "md_path": str(md_path),
            "spoken_md_path": str(md_path),
            "image_root": str(DEFAULT_IMAGE_ROOT),
            "video_root": str(DEFAULT_VIDEO_ROOT),
            "voice_root": str(DEFAULT_VOICE_ROOT),
            "output_root": str(INTERNAL_WORKSPACE_ROOT),
            "status": "active",
        }
    )
    master = sync.sync_master_scheme(project_id, apply_changes=True) if args.sync_master else None
    project = repo.project(project_id)
    products = repo.products(project_id, include_removed=False)
    from .media_workspace import build_media_workspace_plan, ensure_media_workspace
    workspace = ensure_media_workspace(build_media_workspace_plan(project or {}, repo.accounts()))
    _json_out(
        {
            "ok": True,
            "created": existing is None,
            "project_id": project_id,
            "project": project,
            "scheme_product_count": len(products),
            "master": {
                "snapshot_id": master.get("snapshot_id", ""),
                "change_count": master.get("change_count", 0),
                "added": len(master.get("added", [])),
                "updated": len(master.get("updated", [])),
                "reactivated": len(master.get("reactivated", [])),
                "removed": len(master.get("removed", [])),
            }
            if master
            else None,
            "media_workspace": workspace,
            "updated_at": now_iso(),
        }
    )


# ── status ────────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    _, repo, _, wf = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"project does not exist: {args.project_id}")

    products = repo.products(args.project_id, include_removed=False)
    blocks = repo.script_blocks(args.project_id)
    assets = repo.asset_bindings(args.project_id)

    intro_blocks = [b for b in blocks if b.get("script_type") == "intro"]
    product_blocks = [b for b in blocks if b.get("script_type") == "product"]
    voice_assets = [a for a in assets if a.get("asset_type") == "voice"]
    image_assets = [a for a in assets if a.get("asset_type") == "image"]
    video_assets = [a for a in assets if a.get("asset_type") == "video"]

    ready_voices = [a for a in voice_assets if a.get("status") == "ready"]
    ready_images = [a for a in image_assets if a.get("status") == "ready"]

    _json_out({
        "ok": True,
        "project": {
            "id": project["id"],
            "name": project["name"],
            "category": project.get("category", ""),
            "workspace_id": project.get("workspace_id", ""),
            "scheme_id": project.get("scheme_id", ""),
            "scheme_name": project.get("scheme_name", ""),
            "master_snapshot_id": project.get("master_snapshot_id"),
            "master_snapshot_applied_at": project.get("master_snapshot_applied_at"),
            "scheme_product_count": len(products),
        },
        "counts": {
            "products": len(products),
            "intro_blocks": len(intro_blocks),
            "product_blocks": len(product_blocks),
            "voice_ready": len(ready_voices),
            "voice_total": len(voice_assets),
            "image_ready": len(ready_images),
            "image_total": len(image_assets),
            "video_total": len(video_assets),
        },
        "products": [
            {
                "uid": p["uid"],
                "title": p["title"],
                "price_label": p.get("price_label", ""),
            }
            for p in products
        ],
    })


# ── sync ──────────────────────────────────────────────────────────────

def cmd_sync(args: argparse.Namespace) -> None:
    _, _, sync, _ = _init()
    results: dict[str, Any] = {"ok": True}

    if args.step in (None, "master"):
        r = sync.sync_master_scheme(args.project_id)
        results["master"] = {
            "snapshot_id": r.get("snapshot_id", ""),
            "change_count": r.get("change_count", 0),
            "added": len(r.get("added", [])),
            "updated": len(r.get("updated", [])),
            "reactivated": len(r.get("reactivated", [])),
            "removed": len(r.get("removed", [])),
        }

    if args.step in (None, "markdown"):
        r = sync.sync_markdown(args.project_id)
        results["markdown"] = {
            "upserted": r.get("upserted", 0),
            "extra_md": len(r.get("extra_md", [])),
            "missing_copy": len(r.get("missing_copy", [])),
        }

    if args.step in (None, "assets"):
        r = sync.sync_assets(args.project_id, asset_type=args.asset_type)
        results["assets"] = {
            "image": r.get("image", 0),
            "video": r.get("video", 0),
            "voice": r.get("voice", 0),
            "unmatched": r.get("unmatched", 0),
        }

    _json_out(results)


# ── voice ─────────────────────────────────────────────────────────────

def cmd_voice(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    logs: list[str] = []

    result = wf.generate_voice(
        args.project_id,
        account_label=args.account or "",
        voice_provider=args.voice_provider,
        uids=args.uids.split(",") if args.uids else None,
        start_service_if_needed=False,
        progress_hook=lambda msg: logs.append(msg),
    )

    _json_out({
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "logs": logs,
        "stdout": result.stdout,
        "stderr": result.stderr,
    })
    if result.returncode != 0:
        sys.exit(result.returncode)


# ── voice-counts ──────────────────────────────────────────────────────

def cmd_voice_counts(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    total, existing, pending = wf.voice_generation_counts(
        args.project_id,
        account_label=args.account or "",
        voice_provider=args.voice_provider,
    )
    _json_out({
        "ok": True,
        "total": total,
        "existing": existing,
        "pending": pending,
    })


# ── assemble ──────────────────────────────────────────────────────────

def cmd_assemble(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.assemble_spoken_script(
        args.project_id,
        account_label=args.account or "",
        intro_index=args.intro_index,
        mode=args.mode,
        top_uids=args.top_uids,
        product_uids=args.product_uids,
        product_order_strategy=args.product_order_strategy,
        output_markdown_path=args.output or None,
        display_template=args.display_template or "",
    )
    _json_out({
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    })
    if result.returncode != 0:
        sys.exit(result.returncode)


# ── jianying ──────────────────────────────────────────────────────────

def cmd_assemble_plan(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.assemble_spoken_script_plan(
        args.project_id,
        account_label=args.account or "",
        intro_index=args.intro_index,
        mode=args.mode,
        top_uids=args.top_uids,
        product_uids=args.product_uids,
        product_order_strategy=args.product_order_strategy,
    )
    _json_out(result)


def cmd_jianying(args: argparse.Namespace) -> None:
    from .settings import DEFAULT_JIANYING_DRAFT_ROOT

    _, _, _, wf = _init()
    result = wf.generate_jianying_draft(
        args.project_id,
        manifest_path=args.manifest,
        draft_name=args.draft_name,
        draft_root=args.draft_root or str(DEFAULT_JIANYING_DRAFT_ROOT),
        intro_video_path=args.intro_video or None,
        include_subtitles=bool(args.with_subtitles),
        subtitle_no_vad=bool(args.subtitle_no_vad),
    )
    _json_out({
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    })
    if result.returncode != 0:
        sys.exit(result.returncode)


# ── outline ───────────────────────────────────────────────────────────

def cmd_outline(args: argparse.Namespace) -> None:
    from .outline_service import OutlineService

    db, repo, _, _ = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"项目不存在: {args.project_id}")

    service = OutlineService(db)
    result = service.init_or_update_outline(args.project_id, target_path=args.output or None)
    _json_out({
        "ok": True,
        "target_path": result["target_path"],
        "added": len(result["added"]),
        "preserved": len(result["preserved"]),
        "total": result["total"],
    })


# ── scaffold ──────────────────────────────────────────────────────────
def cmd_research_pack(args: argparse.Namespace) -> None:
    from .research_pack_service import ResearchPackService

    db, repo, _, _ = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"project does not exist: {args.project_id}")

    service = ResearchPackService(db)
    result = service.init_or_update_pack(args.project_id, target_path=args.output or None)
    _json_out(
        {
            "ok": True,
            "target_path": result["target_path"],
            "added": len(result["added"]),
            "preserved": len(result["preserved"]),
            "total": result["total"],
        }
    )


def cmd_intro_plan(args: argparse.Namespace) -> None:
    from .intro_plan_writer import write_intro_plan_for_project

    db, _, _, _ = _init()
    result = write_intro_plan_for_project(
        db=db,
        project_id=args.project_id,
        slots_path=args.slots,
        template_id=args.template,
        label=args.label,
        markdown_path=args.markdown or None,
        sync=args.sync,
    )
    _json_out({
        "ok": True,
        "project_id": args.project_id,
        "template": args.template,
        "label": result.label,
        "intro_plan_path": str(result.intro_plan_path),
        "slots_path": str(result.slots_path),
        "markdown_path": str(result.markdown_path),
        "full_script": result.full_script,
        "synced": result.synced,
        "sync_result": result.sync_result,
    })


def cmd_price_transition_plan(args: argparse.Namespace) -> None:
    from .price_transition_plan import write_price_transition_plan_for_project

    db, _, _, _ = _init()
    result = write_price_transition_plan_for_project(
        db=db,
        project_id=args.project_id,
        plan_input_path=args.plan,
        markdown_path=args.markdown or None,
        sync=args.sync,
    )
    _json_out({
        "ok": True,
        "project_id": args.project_id,
        "plan_path": str(result.plan_path),
        "markdown_path": str(result.markdown_path),
        "transition_count": result.transition_count,
        "synced": result.synced,
        "sync_result": result.sync_result,
    })


def cmd_intro_preflight(args: argparse.Namespace) -> None:
    _, repo, _, _ = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"project does not exist: {args.project_id}")

    result = preflight_intro_plan_for_cutme(
        source_plan_path=args.source_plan,
        project=project,
        asset_root=args.asset_root,
        pipeline_path=getattr(args, "pipeline", ""),
    )
    _json_out(result)


def cmd_render_intro_video(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.render_intro_video(
        args.project_id,
        account_label=args.account,
        intro_label=args.intro_label,
        output_path=args.output or None,
        asset_root=args.asset_root,
        pipeline_path=getattr(args, "pipeline", "") or None,
    )
    _json_out(result)


def cmd_scaffold(args: argparse.Namespace) -> None:
    """幂等建立或修复项目完整媒体工作区。"""
    from .media_workspace import build_media_workspace_plan, ensure_media_workspace

    _, repo, _, _ = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"项目不存在: {args.project_id}")

    account = (args.account or "").strip()
    templates = [value.strip() for value in (args.templates or "").split(",") if value.strip()]
    plan = build_media_workspace_plan(
        project,
        repo.accounts(),
        account_filter=account,
        template_overrides=templates or None,
    )
    _json_out({"ok": True, "project_id": args.project_id, "category": project.get("name"), "plan": plan, **ensure_media_workspace(plan)})


def cmd_confirm_production(args: argparse.Namespace) -> None:
    from .artifact_approvals import write_production_confirmation
    from .production_history import ProductionHistoryService

    _, repo, _, _ = _init()
    result = ProductionHistoryService(repo).confirm(
        args.project_id,
        run_manifest_path=args.run_manifest,
        final_path=args.final_path or None,
    )
    if args.pipeline:
        pipeline_path = Path(args.pipeline).expanduser().resolve()
        write_production_confirmation(pipeline_path, result["production"])
        result["pipeline_path"] = str(pipeline_path)
    _json_out(result)


def cmd_confirm_intro_video(args: argparse.Namespace) -> None:
    from .artifact_approvals import confirm_intro_video, sha256_file
    from .utils import now_iso

    source_revision = ""
    if args.source_plan:
        source_plan = Path(args.source_plan).expanduser().resolve()
        if not source_plan.is_file():
            raise ValueError(f"引言 source plan 不存在: {source_plan}")
        source_revision = sha256_file(source_plan)
    approval = confirm_intro_video(
        args.pipeline,
        args.intro_video,
        approved_at=now_iso(),
        source_revision=source_revision,
        source_plan_path=source_plan if args.source_plan else None,
    )
    _json_out({"ok": True, "pipeline_path": str(Path(args.pipeline).resolve()), "approval": approval})


def cmd_materialize_final_script(args: argparse.Namespace) -> None:
    from .final_spoken_script import backfill_final_spoken_script

    _, repo, _, _ = _init()
    _json_out(
        backfill_final_spoken_script(
            repo,
            run_manifest_path=args.run_manifest,
            pipeline_path=args.pipeline or None,
        )
    )


def cmd_production_history(args: argparse.Namespace) -> None:
    from .production_history import ProductionHistoryService

    _, repo, _, _ = _init()
    _json_out(ProductionHistoryService(repo).history(args.project_id, account_label=args.account))


def cmd_cover_context(args: argparse.Namespace) -> None:
    from .cover_workflow import cover_context

    _json_out(cover_context(args.pipeline))


def cmd_record_cover_copy_options(args: argparse.Namespace) -> None:
    from .cover_workflow import record_cover_copy_options

    _json_out(record_cover_copy_options(args.pipeline, options_file=args.options_file))


def cmd_confirm_cover_copy(args: argparse.Namespace) -> None:
    from .cover_workflow import confirm_cover_copy

    _json_out(confirm_cover_copy(args.pipeline, index=args.index))


def cmd_prepare_cover_generation(args: argparse.Namespace) -> None:
    from .cover_workflow import prepare_cover_generation

    _json_out(prepare_cover_generation(args.pipeline))


def cmd_record_cover_image(args: argparse.Namespace) -> None:
    from .cover_workflow import record_cover_image

    _json_out(
        record_cover_image(
            args.pipeline,
            cover_package_path=args.cover_package,
            image_path=args.image,
        )
    )


def cmd_confirm_cover_image(args: argparse.Namespace) -> None:
    from .cover_workflow import confirm_cover_image

    _json_out(confirm_cover_image(args.pipeline))


def cmd_reject_cover_image(args: argparse.Namespace) -> None:
    from .cover_workflow import reject_cover_image

    _json_out(reject_cover_image(args.pipeline, reason=args.reason))


def cmd_rerender_production_preflight(args: argparse.Namespace) -> None:
    from .production_history import ProductionHistoryService

    _, repo, _, _ = _init()
    _json_out(ProductionHistoryService(repo).rerender_preflight(args.production_run_id))


def cmd_rerender_production(args: argparse.Namespace) -> None:
    from .production_history import ProductionHistoryService

    _, repo, _, _ = _init()
    _json_out(
        ProductionHistoryService(repo).rerender(
            args.production_run_id,
            pipeline_path=args.pipeline,
            delivery_dir=args.delivery_dir or None,
        )
    )


def cmd_complete_publishing(args: argparse.Namespace) -> None:
    from .production_history import ProductionHistoryService

    _, repo, _, _ = _init()
    _json_out(
        ProductionHistoryService(repo).complete_publishing(
            args.production_run_id,
            pipeline_path=args.pipeline,
            archive_dir=args.archive_dir or None,
            current_path=args.current_path or None,
        )
    )


def cmd_publishing_context(args: argparse.Namespace) -> None:
    from .production_history import ProductionHistoryService

    _, repo, _, _ = _init()
    _json_out(ProductionHistoryService(repo).publishing_context(args.production_run_id))


def cmd_record_blue_link_backfill(args: argparse.Namespace) -> None:
    from .blue_link_backfill import MasterBlueLinkBackfillClient
    from .production_history import ProductionHistoryService

    _, repo, _, _ = _init()
    service = ProductionHistoryService(repo)
    context = service.publishing_context(args.production_run_id)
    snapshot = MasterBlueLinkBackfillClient(
        workspace_id=args.workspace_id,
        api_base_url=args.master_url,
    ).fetch_browser_pending(args.backfill_id)
    if str(snapshot.get("account_id") or "") != context["master_account_id"]:
        raise ValueError("Master 回流任务账号与本地正式成片账号不一致")
    if str(snapshot.get("scheme_id") or "") != context["scheme_id"]:
        raise ValueError("Master 回流任务方案与本地正式成片方案不一致")
    if str(snapshot.get("video_owner_mid") or "") != context["bilibili_mid"]:
        raise ValueError("Master 回流任务视频作者 MID 与本地账号绑定不一致")
    if snapshot.get("production_run_id") not in (None, "", str(args.production_run_id)):
        raise ValueError("Master 回流任务绑定了另一条本地正式成片记录")
    master_values = {
        "video_url": str(snapshot.get("video_url") or ""),
        "bvid": str(snapshot.get("bvid") or ""),
        "aid": str(snapshot.get("aid") or ""),
        "video_owner_mid": str(snapshot.get("video_owner_mid") or ""),
        "status": str(snapshot.get("status") or ""),
        "matched_count": int(snapshot.get("matched_count") or 0),
        "unresolved_count": int(snapshot.get("unresolved_count") or 0),
    }
    for key, value in {
        "video_url": args.video_url,
        "bvid": args.bvid,
        "aid": args.aid,
        "video_owner_mid": args.owner_mid,
        "status": args.status,
        "matched_count": args.matched_count,
        "unresolved_count": args.unresolved_count,
    }.items():
        if value not in (None, "") and str(value) != str(master_values[key]):
            raise ValueError(f"命令参数 {key} 与 Master 当前结果不一致")
    _json_out(
        service.record_blue_link_backfill(
            args.production_run_id,
            pipeline_path=args.pipeline,
            published_video_url=master_values["video_url"],
            bvid=master_values["bvid"],
            aid=master_values["aid"],
            video_owner_mid=master_values["video_owner_mid"],
            backfill_id=args.backfill_id,
            status=master_values["status"],
            matched_count=master_values["matched_count"],
            unresolved_count=master_values["unresolved_count"],
            browser_pending_count=int(snapshot.get("browser_pending_count") or 0),
            browser_deferred_count=int(snapshot.get("browser_deferred_count") or 0),
            browser_suspended_count=int(snapshot.get("browser_suspended_count") or 0),
            title_candidate_count=int(snapshot.get("title_candidate_count") or 0),
            master_pending_count=int(snapshot.get("master_data_pending_count") or 0),
        )
    )


def cmd_resolve_blue_links(args: argparse.Namespace) -> None:
    from .blue_link_browser import resolve_blue_links

    _json_out(
        resolve_blue_links(
            args.source_link,
            proxy_url=args.cdp_proxy_url or None,
            timeout=args.timeout,
            attempts=args.attempts,
        )
    )


def cmd_blue_link_backfill_report(args: argparse.Namespace) -> None:
    from .blue_link_backfill import get_blue_link_backfill_report

    _json_out(
        get_blue_link_backfill_report(
            args.backfill_id,
            workspace_id=args.workspace_id,
            master_url=args.master_url,
            master_timeout=args.master_timeout,
        )
    )


def cmd_confirm_blue_link_title_candidates(args: argparse.Namespace) -> None:
    from .blue_link_backfill import confirm_blue_link_title_candidates

    payload = json.loads(Path(args.decision_file).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("decision-file 必须是包含版本号、批次 ID 和 decisions 的 JSON 对象")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or any(not isinstance(row, dict) for row in decisions):
        raise ValueError("decision-file 必须是决定数组或包含 decisions 数组的 JSON 对象")
    if int(payload.get("expected_scan_revision") or 0) <= 0:
        raise ValueError("decision-file 缺少有效的 expected_scan_revision")
    if not str(payload.get("decision_batch_id") or "").strip():
        raise ValueError("decision-file 缺少 decision_batch_id")
    _json_out(
        confirm_blue_link_title_candidates(
            args.backfill_id,
            decisions,
            expected_scan_revision=int(payload.get("expected_scan_revision") or 0),
            decision_batch_id=str(payload.get("decision_batch_id") or "").strip(),
            workspace_id=args.workspace_id,
            master_url=args.master_url,
            master_timeout=args.master_timeout,
        )
    )


def cmd_resolve_blue_link_backfill(args: argparse.Namespace) -> None:
    from .blue_link_backfill import resolve_blue_link_backfill

    db, _, _, _ = _init()
    _json_out(
        resolve_blue_link_backfill(
            args.backfill_id,
            workspace_id=args.workspace_id,
            master_url=args.master_url,
            proxy_url=args.cdp_proxy_url or None,
            timeout=args.timeout,
            attempts=args.attempts,
            master_timeout=args.master_timeout,
            max_links=args.max_links,
            jd_min_interval=args.jd_min_interval,
            jd_cooldown_seconds=args.jd_cooldown_seconds,
            database=db,
        )
    )


# ── assets-check ──────────────────────────────────────────────────────

def cmd_assets_check(args: argparse.Namespace) -> None:
    _, repo, sync, _ = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"项目不存在: {args.project_id}")

    r = sync.sync_assets(args.project_id)

    products = repo.products(args.project_id, include_removed=False)
    assets = repo.asset_bindings(args.project_id)

    image_by_uid: dict[str, list[dict]] = {}
    video_by_uid: dict[str, list[dict]] = {}
    for a in assets:
        uid = a.get("uid", "")
        if a.get("asset_type") == "image" and a.get("status") == "ready":
            image_by_uid.setdefault(uid, []).append(a)
        elif a.get("asset_type") == "video" and a.get("status") == "ready":
            video_by_uid.setdefault(uid, []).append(a)

    product_uids = [p["uid"] for p in products]
    missing_images = [
        {"uid": uid, "title": next((p["title"] for p in products if p["uid"] == uid), uid)}
        for uid in product_uids
        if uid not in image_by_uid
    ]
    missing_videos = [
        {"uid": uid, "title": next((p["title"] for p in products if p["uid"] == uid), uid)}
        for uid in product_uids
        if uid not in video_by_uid
    ]

    _json_out({
        "ok": True,
        "total_products": len(product_uids),
        "images_ok": len(product_uids) - len(missing_images),
        "images_missing": len(missing_images),
        "missing_image_items": missing_images,
        "videos_ok": len(product_uids) - len(missing_videos),
        "videos_missing": len(missing_videos),
        "missing_video_items": missing_videos,
        "scanned_roots": r.get("scanned_roots", {}),
    })


# ── parser ────────────────────────────────────────────────────────────

def cmd_render_package(args: argparse.Namespace) -> None:
    validated_phase7_selection(
        args.pipeline,
        required_output=args.output_mode,
        account=args.account,
        product_card_template_id=args.product_card_template_id,
        product_media_mode=args.product_media_mode,
        product_order_strategy=args.product_order_strategy,
        mode=args.mode,
        top_uids=args.top_uids,
    )
    _, _, _, wf = _init()
    result = wf.prepare_product_recommendation_output(
        project_id=args.project_id,
        account_label=args.account,
        output_mode=args.output_mode,
        product_media_mode=args.product_media_mode,
        product_order_strategy=args.product_order_strategy,
        stale_product_image_policy=getattr(args, "stale_product_image_policy", "block"),
        mode=args.mode,
        top_uids=args.top_uids,
        product_card_template_id=args.product_card_template_id,
        package_output_path=args.output or None,
        subtitle_alignment=getattr(args, "subtitle_alignment", "proportional"),
    )
    _json_out(result)


def cmd_product_images(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.regenerate_product_card_images(
        project_id=args.project_id,
        account_label=args.account,
        mode=args.mode,
        product_uid=args.product_uid or "",
        product_card_template_id=args.product_card_template_id or "",
        max_workers=args.workers,
    )
    _json_out(result)


def cmd_template_calibrate(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.template_calibration_probe(
        project_id=args.project_id,
        account_label=args.account,
        product_uid=args.product_uid,
        draft_name=args.draft_name or "",
        draft_root=args.draft_root or None,
        product_media_mode=args.product_media_mode,
        product_card_template_id=args.product_card_template_id or "",
    )
    _json_out(result)


def cmd_template_calibrate_runner(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    load_kwargs = {
        "target_id": args.target or "",
        "include_inactive": args.include_inactive,
    }
    if args.config:
        targets = load_template_calibration_targets(args.config, **load_kwargs)
    else:
        targets = load_template_calibration_targets(**load_kwargs)
    result = run_template_calibration_targets(
        wf,
        targets=targets,
        regenerate_images=args.regenerate_images,
        dry_run=args.dry_run,
        draft_suffix=args.draft_suffix or "",
    )
    _json_out(result)


def cmd_template_doctor(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.template_doctor(
        project_id=args.project_id,
        account_label=args.account,
        product_card_template_id=args.product_card_template_id or "",
        product_media_mode=args.product_media_mode,
    )
    _json_out(result)


def cmd_product_card_preflight(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.product_card_preflight(
        project_id=args.project_id,
        account_label=args.account,
        product_card_template_id=args.product_card_template_id or "",
        product_uid=args.product_uid or "",
        expect_cover=args.expect_cover or "",
    )
    _json_out(result)


def cmd_script_doctor(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.script_doctor(
        project_id=args.project_id,
        intro_label=args.intro_label or "",
    )
    _json_out(result)


def cmd_resource_audit(args: argparse.Namespace) -> None:
    from .resource_lifecycle import audit_project_resources

    db, _, _, _ = _init()
    _json_out(
        audit_project_resources(
            db,
            project_id=args.project_id,
            pipeline_path=args.pipeline or None,
        )
    )


def cmd_resource_reconcile(args: argparse.Namespace) -> None:
    from .resource_lifecycle import reconcile_project_resources

    db, _, _, _ = _init()
    _json_out(
        reconcile_project_resources(
            db,
            project_id=args.project_id,
            pipeline_path=args.pipeline or None,
        )
    )


def cmd_resource_cleanup_list(args: argparse.Namespace) -> None:
    from .resource_lifecycle import assess_cleanup_candidates

    db, _, _, _ = _init()
    _json_out(
        assess_cleanup_candidates(
            db,
            project_id=args.project_id,
            pipeline_path=args.pipeline or None,
            account_label=args.account or "",
            resource_kind=args.kind or "",
        )
    )


def cmd_resource_cleanup_plan(args: argparse.Namespace) -> None:
    from .resource_lifecycle import prepare_cleanup_batch

    db, _, _, _ = _init()
    _json_out(
        prepare_cleanup_batch(
            db,
            project_id=args.project_id,
            pipeline_path=args.pipeline or None,
            account_label=args.account or "",
            resource_kind=args.kind or "",
        )
    )


def cmd_resource_cleanup_delete(args: argparse.Namespace) -> None:
    from .resource_lifecycle import delete_cleanup_batch

    db, _, _, _ = _init()
    _json_out(
        delete_cleanup_batch(
            db,
            batch_id=args.batch_id,
            confirmation_token=args.confirm,
            confirmed_by=args.confirmed_by or "user",
        )
    )


def cmd_resource_history(args: argparse.Namespace) -> None:
    from .resource_lifecycle import list_resource_state_events

    db, _, _, _ = _init()
    _json_out(
        list_resource_state_events(
            db,
            project_id=args.project_id,
            resource_kind=args.kind or "",
            account_label=args.account or "",
            new_state=args.state or "",
            limit=args.limit,
        )
    )


def cmd_copy_lint(args: argparse.Namespace) -> None:
    from .product_copy_lint import diagnose_product_copy_lint

    db, _, _, _ = _init()
    _json_out(diagnose_product_copy_lint(db, project_id=args.project_id))


def cmd_copy_audit(args: argparse.Namespace) -> None:
    from .product_copy_audit import diagnose_product_copy_audit

    db, _, _, _ = _init()
    _json_out(
        diagnose_product_copy_audit(
            db,
            project_id=args.project_id,
            voice_profile=args.voice_profile,
        )
    )


def cmd_workflow_doctor(args: argparse.Namespace) -> None:
    exit_code = 0
    try:
        _, _, _, wf = _init()
        result = wf.workflow_doctor(
            args.project_ref,
            account_label=args.account or "",
            scheme_name=args.scheme_name or "",
            intro_label=args.intro_label or "",
            intro_index=args.intro_index,
            mode=args.mode,
            top_uids=args.top_uids,
            product_order_strategy=args.product_order_strategy,
            product_card_template_id=args.product_card_template_id or "",
            product_media_mode=args.product_media_mode,
        )
        payload = build_workflow_observation(result)
    except ProjectNotFoundError:
        payload = build_workflow_observation_error(
            "project_not_found",
            "The requested project could not be resolved.",
        )
        exit_code = 1
    except AmbiguousProjectReferenceError:
        payload = build_workflow_observation_error(
            "ambiguous_project_reference",
            "The project reference matches multiple projects.",
        )
        exit_code = 1
    except InvalidWorkflowRequestError:
        payload = build_workflow_observation_error(
            "workflow_doctor_invalid_request",
            "The workflow diagnosis request is invalid.",
        )
        exit_code = 1
    except Exception:
        payload = build_workflow_observation_error(
            "workflow_doctor_internal_error",
            "Workflow diagnosis failed unexpectedly.",
        )
        exit_code = 1
    _json_out(payload)
    if exit_code:
        raise SystemExit(exit_code)


def cmd_materialize_episode(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.materialize_episode_markdown(
        project_id=args.project_id,
        library_path=args.library_path or None,
    )
    _json_out(result)


def cmd_render_final_video(args: argparse.Namespace) -> None:
    from .final_video_pipeline import run_final_video_pipeline

    _, _, _, wf = _init()
    intro_video_text = getattr(args, "intro_video_text", "") or ""
    intro_video_text_file = getattr(args, "intro_video_text_file", "") or ""
    if intro_video_text_file:
        intro_video_text = Path(intro_video_text_file).read_text(encoding="utf-8-sig")
    result = run_final_video_pipeline(
        wf,
        project_id=args.project_id,
        account_label=args.account,
        product_media_mode=args.product_media_mode,
        product_order_strategy=args.product_order_strategy,
        product_image_mode=args.product_image_mode,
        stale_product_image_policy=args.stale_product_image_policy,
        mode=args.mode,
        top_uids=args.top_uids,
        product_card_template_id=args.product_card_template_id or "",
        package_output_path=args.package_output or None,
        output_path=args.output or None,
        delivery_dir=args.delivery_dir or None,
        intro_video_path=args.intro_video or None,
        intro_video_text=intro_video_text,
        intro_video_source_plan_path=getattr(args, "intro_video_source_plan", "") or None,
        full_output_path=args.full_output or None,
        pipeline_path=getattr(args, "pipeline", "") or None,
        acceptance_mode=args.acceptance_mode,
        subtitle_alignment=getattr(args, "subtitle_alignment", "asr"),
    )
    _json_out(result)


def cmd_confirm_phase7_selection(args: argparse.Namespace) -> None:
    result = confirm_phase7_selection(
        args.pipeline,
        output_branch=args.output_branch,
        account=args.account,
        product_card_template_id=args.product_card_template_id,
        product_media_mode=args.product_media_mode,
        product_order_strategy=args.product_order_strategy,
        mode=args.mode,
        top_uids=args.top_uids,
    )
    _json_out(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bworkflow_sql",
        description="B-Workflow SQL Headless CLI",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # projects
    sub.add_parser("projects", help="列出所有项目")

    # create-project
    p = sub.add_parser("create-project", help="按 Master workspace/category/scheme 创建或更新本地项目")
    p.add_argument("--name", required=True, help="本地项目名，如 数码-桌面音响")
    p.add_argument("--workspace-id", required=True)
    p.add_argument("--workspace-name", default="赵二")
    p.add_argument("--category-parent-id", default="")
    p.add_argument("--category-parent-name", default="")
    p.add_argument("--category-id", required=True)
    p.add_argument("--category-name", required=True)
    p.add_argument("--scheme-id", required=True)
    p.add_argument("--scheme-name", required=True)
    p.add_argument("--md-path", default="", help="绑定的可复用商品文案资产 Markdown 路径")
    p.add_argument("--sync-master", action="store_true", help="创建后立即同步 Master 方案商品")

    # status
    p = sub.add_parser("status", help="项目状态概览")
    p.add_argument("project_id", type=int)

    # sync
    p = sub.add_parser("sync", help="同步 Master / MD / 素材")
    p.add_argument("project_id", type=int)
    p.add_argument("--step", choices=["master", "markdown", "assets"])
    p.add_argument("--asset-type", choices=["image", "video", "voice"])

    # voice
    p = sub.add_parser("voice", help="批量生成配音")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音账户标签（如 小博）")
    p.add_argument("--uids", help="指定商品 UID，逗号分隔")
    p.add_argument(
        "--voice-provider",
        choices=[VOICE_PROVIDER_MINIMAX, VOICE_PROVIDER_INDEXTTS],
        default=VOICE_PROVIDER_MINIMAX,
        help="配音实现；默认 minimax，传 indextts 可切回本地服务",
    )

    # voice-counts
    p = sub.add_parser("voice-counts", help="配音生成数量预览")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音账户标签")
    p.add_argument(
        "--voice-provider",
        choices=[VOICE_PROVIDER_MINIMAX, VOICE_PROVIDER_INDEXTTS],
        default=VOICE_PROVIDER_MINIMAX,
        help="按指定配音实现计算可复用与待生成数量",
    )

    # assemble
    p = sub.add_parser("assemble", help="组合口播稿")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音账户标签")
    p.add_argument("--intro-index", type=int, default=1, help="引言版本号（1-based）")
    p.add_argument("--output", "-o", help="口播稿输出路径")
    p.add_argument("--display-template", default="")
    p.add_argument("--mode", choices=["standard", "top"], default="standard")
    p.add_argument("--top-uids", default="", help="comma-separated product UIDs pinned to the top")
    p.add_argument("--product-uids", default="", help="comma-separated complete product order; disables reshuffling")
    p.add_argument("--product-order-strategy", choices=["price_segment_shuffle", "stable"], default="price_segment_shuffle")

    p = sub.add_parser("assemble-plan", help="Preview spoken-script assembly without writing files")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音账户标签")
    p.add_argument("--intro-index", type=int, default=1, help="intro version index, 1-based")
    p.add_argument("--mode", choices=["standard", "top"], default="standard")
    p.add_argument("--top-uids", default="", help="comma-separated product UIDs pinned to the top")
    p.add_argument("--product-uids", default="", help="comma-separated complete product order; disables reshuffling")
    p.add_argument("--product-order-strategy", choices=["price_segment_shuffle", "stable"], default="price_segment_shuffle")

    # jianying
    p = sub.add_parser("jianying", help="生成剪映草稿")
    p.add_argument("project_id", type=int)
    p.add_argument("--manifest", required=True, help="口播稿 manifest 路径")
    p.add_argument("--draft-name", required=True, help="草稿名称")
    p.add_argument("--draft-root", help="剪映草稿根目录")
    p.add_argument("--intro-video", help="引言视频 MP4 路径")
    p.add_argument("--with-subtitles", action="store_true", help="生成剪映草稿时同步生成文本字幕轨")
    p.add_argument("--subtitle-no-vad", action="store_true", help="字幕 ASR 不启用 VAD，兼容 onnxruntime 不可用的环境")

    # outline
    p = sub.add_parser("outline", help="创建/更新文案 MD 骨架（价格段自动从 Master scheme 派生）")
    p.add_argument("project_id", type=int)
    p.add_argument("--output", "-o", help="MD 输出路径（默认按品类名生成）")

    p = sub.add_parser("research-pack", help="Create/update the category+scheme research evidence pack skeleton")
    p.add_argument("project_id", type=int)
    p.add_argument("--output", "-o", help="research pack output path; defaults to WriteSpace 0.资料采集包")

    # intro-plan
    p = sub.add_parser("intro-plan", help="用 CutMe 引言模板槽位生成文案和 intro_plan")
    p.add_argument("project_id", type=int)
    p.add_argument("--slots", required=True, help="引言槽位 JSON 文件")
    p.add_argument("--template", default="pain_avoidance_priority_v1", help="CutMe 引言模板 ID")
    p.add_argument("--label", default="引言1", help="写入 Markdown 的引言版本标题")
    p.add_argument("--markdown", help="覆盖写入目标 MD；默认使用项目 md_path 或文案骨架默认路径")
    p.add_argument("--sync", action="store_true", help="写入 Markdown 后立即同步入库")

    p = sub.add_parser("price-transition-plan", help="用结构化计划生成价格过渡文案和自动剪辑卡片源计划")
    p.add_argument("project_id", type=int)
    p.add_argument("--plan", required=True, help="价格过渡结构化计划 JSON 文件")
    p.add_argument("--markdown", help="覆盖写入目标 MD；默认使用项目文案路径")
    p.add_argument("--sync", action="store_true", help="写入 Markdown 后立即同步入库；仅限用户确认定稿后")

    p = sub.add_parser("intro-preflight", help="Check CutMe intro template and material gates before rendering")
    p.add_argument("project_id", type=int)
    p.add_argument("--source-plan", required=True, help="source-intro-plan JSON path")
    p.add_argument("--asset-root", default=str(Path("G:/2026项目-b站/素材-自动剪辑")), help="intro material root")
    p.add_argument("--pipeline", default="", help="optional .pipeline.json path to record phase-6 preflight status")

    p = sub.add_parser("render-intro-video", help="Render standalone phase-6 intro MP4 without temporary subtitles")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True, help="voice/account label")
    p.add_argument("--intro-label", default="引言1", help="intro block label, for example 引言1")
    p.add_argument("--output", "-o", help="intro MP4 output path; defaults to the project intro workspace")
    p.add_argument("--asset-root", default=str(DEFAULT_INTRO_ASSET_ROOT), help="intro material root")
    p.add_argument("--pipeline", default="", help=".pipeline.json path; creates/reuses its project delivery directory")

    p = sub.add_parser("materialize-final-script", help="从最终 RenderPackage 生成并绑定成片完整口播稿")
    p.add_argument("--run-manifest", required=True, help="final-video run manifest 路径")
    p.add_argument("--pipeline", default="", help="可选 .pipeline.json；同步写入完整稿证据")

    p = sub.add_parser("cover-context", help="Read confirmed production and final spoken script for cover-copy generation")
    p.add_argument("--pipeline", required=True)

    p = sub.add_parser("record-cover-copy-options", help="Persist exactly five AI-generated cover-copy options")
    p.add_argument("--pipeline", required=True)
    p.add_argument("--options-file", required=True, help="UTF-8 JSON array or object containing options")

    p = sub.add_parser("confirm-cover-copy", help="Confirm one cover-copy option")
    p.add_argument("--pipeline", required=True)
    p.add_argument("--index", required=True, type=int, help="1-based option index")

    p = sub.add_parser("prepare-cover-generation", help="Freeze portrait, account style, and model-native cover prompt")
    p.add_argument("--pipeline", required=True)

    p = sub.add_parser("record-cover-image", help="Record the single generated 4:3 cover candidate")
    p.add_argument("--pipeline", required=True)
    p.add_argument("--cover-package", required=True)
    p.add_argument("--image", required=True)

    p = sub.add_parser("confirm-cover-image", help="Accept the cover image and release publishing preparation")
    p.add_argument("--pipeline", required=True)

    p = sub.add_parser("reject-cover-image", help="Reject the current cover candidate and regenerate")
    p.add_argument("--pipeline", required=True)
    p.add_argument("--reason", required=True)

    # scaffold
    p = sub.add_parser("scaffold", help="建立或修复完整媒体工作区")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="只修复指定账号；不传则处理所有启用账号")
    p.add_argument("--templates", help="覆盖商品图模板子目录（逗号分隔）；默认按配音员在 template_config 的坐标模板自动取")

    p = sub.add_parser("confirm-production", help="将已验收完整 MP4 确认为正式成片")
    p.add_argument("project_id", type=int)
    p.add_argument("--run-manifest", required=True, help="final-video run manifest 路径")
    p.add_argument("--final-path", default="", help="显式确认后续剪辑导出的最终发布版 MP4；模板与生成来源仍取 run manifest")
    p.add_argument("--pipeline", default="", help="可选 .pipeline.json；写入正式确认凭证")

    p = sub.add_parser("confirm-intro-video", help="将用户验收的引言 MP4 绑定到哈希确认凭证")
    p.add_argument("--pipeline", required=True, help="当前项目 .pipeline.json")
    p.add_argument("--intro-video", required=True, help="已验收引言 MP4 路径")
    p.add_argument("--source-plan", default="", help="可选 source-intro-plan JSON；写入来源版本哈希")

    p = sub.add_parser("confirm-phase7-selection", help="记录用户明确确认的阶段 7 输出、媒体、排序和模板选择")
    p.add_argument("--pipeline", required=True, help="当前项目 .pipeline.json")
    p.add_argument("--output-branch", choices=["jianying_draft", "final_mp4", "both"], required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--product-card-template-id", required=True)
    p.add_argument("--product-media-mode", choices=["cover_only", "video_preferred"], required=True)
    p.add_argument(
        "--product-order-strategy",
        choices=["price_segment_shuffle", "stable"],
        default="price_segment_shuffle",
    )
    p.add_argument("--mode", choices=["standard", "top"], default="standard")
    p.add_argument("--top-uids", default="", help="top 模式商品 UID，逗号分隔")

    p = sub.add_parser("production-history", help="查询正式成片模板历史与未使用模板推荐")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True)

    p = sub.add_parser("rerender-production-preflight", help="只读检查正式成片冻结配方和全部源文件")
    p.add_argument("production_run_id", type=int)

    p = sub.add_parser("rerender-production", help="仅按冻结配方重渲染并生成待确认候选片")
    p.add_argument("production_run_id", type=int)
    p.add_argument("--pipeline", required=True, help="当前项目 .pipeline.json；已发布状态不会被降级")
    p.add_argument("--delivery-dir", default="", help="覆盖 pipeline 中的正式交付目录")

    p = sub.add_parser("complete-publishing", help="记录发布完成并整体归档或重绑已发布项目目录")
    p.add_argument("production_run_id", type=int)
    p.add_argument("--pipeline", required=True, help="当前项目 .pipeline.json")
    destination = p.add_mutually_exclusive_group()
    destination.add_argument("--archive-dir", default="", help="覆盖项目目录的最终归档路径；默认使用当前月份目录/原项目目录名")
    destination.add_argument("--current-path", default="", help="项目目录已整体手工移动时，传入其中正式成片的当前完整路径")

    p = sub.add_parser("publishing-context", help="读取正式成片绑定的 Master 账号 ID、B站 MID 和方案 ID")
    p.add_argument("production_run_id", type=int)

    p = sub.add_parser("record-blue-link-backfill", help="记录 Master 蓝链回流结果并更新 pipeline 状态")
    p.add_argument("production_run_id", type=int)
    p.add_argument("--pipeline", required=True)
    p.add_argument("--backfill-id", required=True)
    p.add_argument("--workspace-id", required=True, help="Master workspace UUID")
    p.add_argument("--master-url", default=DEFAULT_MASTER_API_BASE_URL)
    p.add_argument("--video-url", default="", help="兼容校验参数；真实值以 Master 为准")
    p.add_argument("--bvid", default="", help="兼容校验参数；真实值以 Master 为准")
    p.add_argument("--aid", default="", help="兼容校验参数；真实值以 Master 为准")
    p.add_argument("--owner-mid", default="", help="兼容校验参数；真实值以 Master 为准")
    p.add_argument("--status", choices=["complete", "partial"], default=None)
    p.add_argument("--matched-count", type=int, default=None)
    p.add_argument("--unresolved-count", type=int, default=None)

    p = sub.add_parser("resolve-blue-links", help="用登录态 Chrome 确定性解析商品中转页")
    p.add_argument("--source-link", action="append", required=True, help="待解析蓝链；可重复传入")
    p.add_argument(
        "--cdp-proxy-url",
        default="",
        help="CDP HTTP 代理；默认读取 BWORKFLOW_CDP_PROXY_URL 或 127.0.0.1:3456",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="每条链接等待标准商品页的秒数")
    p.add_argument("--attempts", type=int, default=1, help="单链接诊断尝试次数；默认 1")

    p = sub.add_parser(
        "blue-link-backfill-report",
        help="只读查询已持久化的未完成蓝链，并按类型返回每类最多三条样本",
    )
    p.add_argument("backfill_id", help="Master 蓝链回流任务 UUID")
    p.add_argument("--workspace-id", required=True, help="Master workspace UUID")
    p.add_argument("--master-url", default=DEFAULT_MASTER_API_BASE_URL, help="Master API 地址")
    p.add_argument("--master-timeout", type=float, default=30.0, help="Master API 请求超时秒数")

    p = sub.add_parser(
        "confirm-blue-link-title-candidates",
        help="批量提交用户确认或拒绝的标题候选；不直接写本地数据库",
    )
    p.add_argument("backfill_id", help="Master 蓝链回流任务 UUID")
    p.add_argument("--workspace-id", required=True, help="Master workspace UUID")
    p.add_argument("--decision-file", required=True, help="UTF-8 JSON 决定文件")
    p.add_argument("--master-url", default=DEFAULT_MASTER_API_BASE_URL, help="Master API 地址")
    p.add_argument("--master-timeout", type=float, default=30.0, help="Master API 请求超时秒数")

    p = sub.add_parser(
        "resolve-blue-link-backfill",
        help="按 Master backfill_id 自动拉取、浏览器解析并回传挂起蓝链",
    )
    p.add_argument("backfill_id", help="Master 蓝链回流任务 UUID")
    p.add_argument("--workspace-id", required=True, help="Master workspace UUID")
    p.add_argument(
        "--master-url",
        default=DEFAULT_MASTER_API_BASE_URL,
        help="Master API 地址",
    )
    p.add_argument(
        "--cdp-proxy-url",
        default="",
        help="CDP HTTP 代理；默认读取 BWORKFLOW_CDP_PROXY_URL 或 127.0.0.1:3456",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="每条链接等待标准商品页的秒数")
    p.add_argument("--attempts", type=int, default=1, help="兼容参数；批处理固定每条一次")
    p.add_argument("--master-timeout", type=float, default=30.0, help="Master API 请求超时秒数")
    p.add_argument("--max-links", type=int, default=5, help="单次任务最多实际打开的链接数")
    p.add_argument("--jd-min-interval", type=float, default=20.0, help="京东链接最小访问间隔秒数")
    p.add_argument("--jd-cooldown-seconds", type=float, default=7200.0, help="京东 403 熔断时长")

    # assets-check
    p = sub.add_parser("assets-check", help="素材完整性检查")
    p.add_argument("project_id", type=int)

    p = sub.add_parser("render-package", help="Generate Remotion RenderPackage")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True)
    p.add_argument("--pipeline", required=True, help="包含用户阶段 7 选择确认的 .pipeline.json")
    p.add_argument(
        "--output-mode",
        choices=["jianying_draft", "final_mp4"],
        default="jianying_draft",
    )
    p.add_argument(
        "--product-media-mode",
        choices=["cover_only", "video_preferred"],
        required=True,
        help="product display media: cover_only uses only the cover image; video_preferred uses product video when available",
    )
    p.add_argument(
        "--product-order-strategy",
        choices=["price_segment_shuffle", "stable"],
        default="price_segment_shuffle",
        help="product order strategy: price_segment_shuffle shuffles products within each price segment; stable keeps the synced order",
    )
    p.add_argument(
        "--stale-product-image-policy",
        choices=["block", "reuse"],
        default="block",
        help="block when product-card image fingerprints are stale, or explicitly reuse old images",
    )
    p.add_argument(
        "--mode",
        choices=["standard", "top"],
        default="standard",
        help="segment order mode: standard groups by price range; top puts --top-uids first",
    )
    p.add_argument("--top-uids", default="", help="top mode product UIDs, comma separated")
    p.add_argument(
        "--product-card-template-id",
        required=True,
        help="explicitly confirmed Remotion-first product-card template id or display name",
    )
    p.add_argument(
        "--subtitle-alignment",
        choices=["proportional", "asr"],
        default="proportional",
        help="final_mp4 subtitle timing: asr uses exact-transcript forced alignment; proportional is a manual fallback",
    )
    p.add_argument("--output", "-o", help="render-package.json output path")

    p = sub.add_parser("render-final-video", help="Generate final MP4 through RenderPackage and CutMe")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True)
    p.add_argument(
        "--product-media-mode",
        choices=["cover_only", "video_preferred"],
        required=True,
        help="cover_only uses only the cover image; video_preferred uses product video when available",
    )
    p.add_argument(
        "--product-order-strategy",
        choices=["price_segment_shuffle", "stable"],
        default="price_segment_shuffle",
        help="price_segment_shuffle shuffles products within each price segment; stable keeps the synced order",
    )
    p.add_argument(
        "--product-image-mode",
        choices=["skip", "missing", "stale", "all"],
        default="missing",
        help="missing creates absent product-card images before rendering; stale/all regenerate changed images; skip only checks package inputs",
    )
    p.add_argument(
        "--stale-product-image-policy",
        choices=["block", "reuse"],
        default="block",
        help="block when product-card image fingerprints are stale, or explicitly reuse old images",
    )
    p.add_argument(
        "--mode",
        choices=["standard", "top"],
        default="standard",
        help="segment order mode: standard groups by price range; top puts --top-uids first",
    )
    p.add_argument("--top-uids", default="", help="top mode product UIDs, comma separated")
    p.add_argument(
        "--product-card-template-id",
        required=True,
        help="explicitly confirmed Remotion-first product-card template id or display name",
    )
    p.add_argument("--package-output", help="render-package.json output path")
    p.add_argument("--output", "-o", help="final mp4 output path")
    p.add_argument(
        "--delivery-dir",
        default="",
        help="standard delivery directory: MP4s go at root, evidence/process files go into subdirectories",
    )
    p.add_argument("--intro-video", help="accepted intro MP4 to prepend to the product recommendation MP4")
    p.add_argument("--intro-video-text", default="", help="intro spoken text for the final video's unified subtitles")
    p.add_argument("--intro-video-text-file", default="", help="UTF-8 intro text file for the final video's unified subtitles")
    p.add_argument("--intro-video-source-plan", default="", help="source-intro-plan JSON; preferred for intro subtitle scene splitting")
    p.add_argument("--full-output", help="full MP4 output path when --intro-video is provided")
    p.add_argument("--pipeline", required=True, help=".pipeline.json with an explicit user-confirmed phase 7 selection")
    p.add_argument(
        "--subtitle-alignment",
        choices=["proportional", "asr"],
        default="asr",
        help="subtitle timing: asr uses exact-transcript forced alignment; proportional is an explicit manual fallback",
    )
    p.add_argument(
        "--acceptance-mode",
        choices=["none", "quick", "visual", "full"],
        default="full",
        help="none/quick are fast checks; visual extracts frames; full also runs loudnorm",
    )

    p = sub.add_parser("product-images", help="Regenerate Remotion product-card images")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True)
    p.add_argument(
        "--mode",
        choices=["stale", "missing", "all"],
        default="stale",
        help="stale regenerates changed product cards; missing creates absent account images; all regenerates both ready and missing images",
    )
    p.add_argument("--product-uid", default="", help="只重生成指定商品 UID 的商品图")
    p.add_argument(
        "--product-card-template-id",
        required=True,
        help="Remotion-first product-card template id or display name; required for still/product-image generation",
    )
    p.add_argument(
        "--workers",
        type=int,
        choices=range(1, 5),
        default=3,
        help="bounded parallel render workers (1-4); default: 3",
    )

    p = sub.add_parser("resource-audit", help="Read-only resource lifecycle and orphan audit")
    p.add_argument("project_id", type=int)
    p.add_argument("--pipeline", default="", help="optional current .pipeline.json path")

    p = sub.add_parser("resource-reconcile", help="Register safe cleanup candidates without deleting files")
    p.add_argument("project_id", type=int)
    p.add_argument("--pipeline", default="", help="optional current .pipeline.json path")

    p = sub.add_parser("resource-cleanup-list", help="List candidates that pass or fail permanent-delete gates")
    p.add_argument("project_id", type=int)
    p.add_argument("--pipeline", default="", help="optional current .pipeline.json path")
    p.add_argument("--account", default="", help="optional exact account label")
    p.add_argument("--kind", default="", help="optional exact resource kind")

    p = sub.add_parser("resource-cleanup-plan", help="Prepare a fingerprinted permanent-delete batch")
    p.add_argument("project_id", type=int)
    p.add_argument("--pipeline", default="", help="optional current .pipeline.json path")
    p.add_argument("--account", default="", help="optional exact account label")
    p.add_argument("--kind", default="", help="optional exact resource kind")

    p = sub.add_parser("resource-cleanup-delete", help="Permanently delete one explicitly confirmed batch")
    p.add_argument("--batch-id", required=True)
    p.add_argument("--confirm", required=True, help="one-time token returned by resource-cleanup-plan")
    p.add_argument("--confirmed-by", default="user", help="audit label for the confirming operator")

    p = sub.add_parser("resource-history", help="Query append-only resource creation, change, invalidation, and deletion events")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", default="", help="optional exact account label")
    p.add_argument("--kind", default="", help="optional exact resource kind")
    p.add_argument("--state", default="", help="optional exact new state")
    p.add_argument("--limit", type=int, default=200)

    p = sub.add_parser("product-card-preflight", help="Preflight product-card data and image freshness before product-images or phase-7 output")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True)
    p.add_argument(
        "--product-card-template-id",
        required=True,
        help="explicit Remotion-first product-card template id or display name",
    )
    p.add_argument("--product-uid", default="", help="optional single product UID to check")
    p.add_argument("--expect-cover", default="", help="optional filename/substring expected in coverAsset or dataMap.cover")

    p = sub.add_parser("template-calibrate", help="生成单商品剪映模板位置校准草稿")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True, help="账号/用户标签，如 小燃")
    p.add_argument("--product-uid", required=True, help="用于校准的商品 UID")
    p.add_argument("--draft-name", default="", help="校准草稿名称")
    p.add_argument("--draft-root", default="", help="剪映草稿根目录")
    p.add_argument(
        "--product-card-template-id",
        required=True,
        help="Remotion-first product-card template id or display name to calibrate",
    )
    p.add_argument(
        "--product-media-mode",
        choices=["video_preferred"],
        default="video_preferred",
        help="模板校准必须使用商品视频模式",
    )

    p = sub.add_parser("template-calibrate-runner", help="Run standard checklist-based template calibration")
    p.add_argument(
        "--target",
        default="",
        help="Target id from config/template-calibration-targets.json; omit to run all active targets",
    )
    p.add_argument("--config", default="", help="Override calibration target config path")
    p.add_argument("--draft-suffix", default="", help="Append suffix to generated draft names, for example v3")
    p.add_argument("--dry-run", action="store_true", help="Run doctor checks without generating Jianying drafts")
    p.add_argument("--include-inactive", action="store_true", help="Allow inactive targets from the checklist")
    p.add_argument(
        "--no-regenerate-images",
        dest="regenerate_images",
        action="store_false",
        help="Do not run product-images automatically when doctor reports image issues",
    )
    p.set_defaults(regenerate_images=True)

    p = sub.add_parser("template-doctor", help="Diagnose product-card template/image/video-slot issues")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True)
    p.add_argument(
        "--product-card-template-id",
        default="",
        help="explicit Remotion-first product-card template id or display name to diagnose",
    )
    p.add_argument(
        "--product-media-mode",
        choices=["cover_only", "video_preferred"],
        default="video_preferred",
        help="media mode to diagnose; video_preferred checks video-slot readiness",
    )

    p = sub.add_parser("copy-lint", help="Check product narration for internal labels and research-process wording")
    p.add_argument("project_id", type=int)

    p = sub.add_parser("copy-audit", help="Report non-blocking product-copy voice and document-structure warnings")
    p.add_argument("project_id", type=int)
    p.add_argument("--voice-profile", default="zhaoer", help="soft-audit voice profile id")

    p = sub.add_parser("script-doctor", help="Diagnose phase-3 copy units, intro plan matching, and script_blocks sync")
    p.add_argument("project_id", type=int)
    p.add_argument("--intro-label", default="", help="selected intro version label, for example 引言1")

    p = sub.add_parser("workflow-doctor", help="Diagnose script, voice assembly, and optional template readiness")
    p.add_argument("project_ref", help="project id, project/category name, or partial category name")
    p.add_argument("--account", default="", help="voice/account label for voice and assembly checks")
    p.add_argument("--scheme-name", default="", help="optional scheme name filter when project/category name matches multiple projects")
    p.add_argument("--intro-label", default="", help="selected intro version label for script-doctor")
    p.add_argument("--intro-index", type=int, default=1, help="intro version index for assemble-plan, 1-based")
    p.add_argument("--mode", choices=["standard", "top"], default="standard")
    p.add_argument("--top-uids", default="", help="comma-separated product UIDs pinned to the top")
    p.add_argument("--product-order-strategy", choices=["price_segment_shuffle", "stable"], default="price_segment_shuffle")
    p.add_argument(
        "--product-card-template-id",
        default="",
        help="optional Remotion-first product-card template id or display name to include template-doctor",
    )
    p.add_argument(
        "--product-media-mode",
        choices=["cover_only", "video_preferred"],
        default="video_preferred",
        help="product media mode for template diagnostics",
    )

    p = sub.add_parser("materialize-episode", help="Normalize reusable copy into the project's asset Markdown")
    p.add_argument("project_id", type=int)
    p.add_argument("--library-path", default="", help="override reusable product-copy library Markdown path")

    return parser


DISPATCH = {
    "projects": cmd_projects,
    "create-project": cmd_create_project,
    "status": cmd_status,
    "sync": cmd_sync,
    "voice": cmd_voice,
    "voice-counts": cmd_voice_counts,
    "assemble": cmd_assemble,
    "assemble-plan": cmd_assemble_plan,
    "jianying": cmd_jianying,
    "outline": cmd_outline,
    "research-pack": cmd_research_pack,
    "intro-plan": cmd_intro_plan,
    "price-transition-plan": cmd_price_transition_plan,
    "intro-preflight": cmd_intro_preflight,
    "render-intro-video": cmd_render_intro_video,
    "scaffold": cmd_scaffold,
    "confirm-production": cmd_confirm_production,
    "confirm-intro-video": cmd_confirm_intro_video,
    "materialize-final-script": cmd_materialize_final_script,
    "cover-context": cmd_cover_context,
    "record-cover-copy-options": cmd_record_cover_copy_options,
    "confirm-cover-copy": cmd_confirm_cover_copy,
    "prepare-cover-generation": cmd_prepare_cover_generation,
    "record-cover-image": cmd_record_cover_image,
    "confirm-cover-image": cmd_confirm_cover_image,
    "reject-cover-image": cmd_reject_cover_image,
    "confirm-phase7-selection": cmd_confirm_phase7_selection,
    "production-history": cmd_production_history,
    "rerender-production-preflight": cmd_rerender_production_preflight,
    "rerender-production": cmd_rerender_production,
    "complete-publishing": cmd_complete_publishing,
    "publishing-context": cmd_publishing_context,
    "record-blue-link-backfill": cmd_record_blue_link_backfill,
    "resolve-blue-links": cmd_resolve_blue_links,
    "blue-link-backfill-report": cmd_blue_link_backfill_report,
    "confirm-blue-link-title-candidates": cmd_confirm_blue_link_title_candidates,
    "resolve-blue-link-backfill": cmd_resolve_blue_link_backfill,
    "assets-check": cmd_assets_check,
    "render-package": cmd_render_package,
    "render-final-video": cmd_render_final_video,
    "product-images": cmd_product_images,
    "resource-audit": cmd_resource_audit,
    "resource-reconcile": cmd_resource_reconcile,
    "resource-cleanup-list": cmd_resource_cleanup_list,
    "resource-cleanup-plan": cmd_resource_cleanup_plan,
    "resource-cleanup-delete": cmd_resource_cleanup_delete,
    "resource-history": cmd_resource_history,
    "product-card-preflight": cmd_product_card_preflight,
    "template-calibrate": cmd_template_calibrate,
    "template-calibrate-runner": cmd_template_calibrate_runner,
    "template-doctor": cmd_template_doctor,
    "copy-lint": cmd_copy_lint,
    "copy-audit": cmd_copy_audit,
    "script-doctor": cmd_script_doctor,
    "workflow-doctor": cmd_workflow_doctor,
    "materialize-episode": cmd_materialize_episode,
}


def main() -> None:
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        DISPATCH[args.command](args)
    except Phase7SelectionError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "blocked",
                    "error": {"code": exc.code, "message": str(exc), "retryable": False},
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    except (ValueError, FileNotFoundError) as exc:
        _json_err(str(exc))
    except CutMeAdapterError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "repair_required",
                    "error": {
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                        "diagnostic": exc.stderr,
                    },
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception:
        _json_err(traceback.format_exc())


if __name__ == "__main__":
    main()
