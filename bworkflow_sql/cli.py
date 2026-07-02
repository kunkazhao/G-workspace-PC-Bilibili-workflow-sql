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
                "updated_at": p.get("updated_at", ""),
            }
            for p in projects
        ],
    })


# ── status ────────────────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> None:
    _, repo, _, wf = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"项目不存在: {args.project_id}")

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
            "added": len(r.get("added", [])),
            "updated": len(r.get("updated", [])),
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
        voice_provider="minimax",
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


def cmd_scaffold(args: argparse.Namespace) -> None:
    """为项目预建素材目录骨架（商品图 / 配音 / Roll-B）。

    目录基于项目记录的 root + 品类名（项目 name，如 家居-速干衣）+ 配音员：
      商品图：{image_root}/{品类}/{配音员}/{模板}    模板 = 该配音员在 template_config 里的坐标模板
      配音：  {voice_root}/{品类}/{配音员}
      Roll-B：{video_root}/{品类}                  （不依赖配音员）

    模板目录严格按 template_config.USER_TEMPLATES 建，不硬编码数量：
    每个模板对应剪映草稿生成时的展示坐标（TEMPLATE_COORDS），建多余的模板目录
    会和坐标表对不上，草稿生成时取不到坐标。

    未指定 --account 时只建 Roll-B，待配音员确定后再补建带配音员的目录。
    """
    from .template_config import available_templates, image_set_for_template

    _, repo, _, _ = _init()
    project = repo.project(args.project_id)
    if not project:
        _json_err(f"项目不存在: {args.project_id}")

    category = (project.get("name") or "").strip()
    if not category:
        _json_err("项目缺少品类名（name），无法建目录")

    account = (args.account or "").strip()
    # 模板默认按配音员在 template_config 里的坐标模板取，不硬编码。
    # --templates 显式传入时覆盖（应急用）。
    template_warning = ""
    if args.templates:
        templates = [t.strip() for t in args.templates.split(",") if t.strip()]
    elif account:
        templates = [image_set_for_template(t) for t in available_templates(account)]
        if not templates:
            template_warning = f"配音员「{account}」不在 template_config.USER_TEMPLATES 中，未建任何模板目录，请先在坐标表里登记其模板"
    else:
        templates = []

    image_root = (project.get("image_root") or "").strip()
    voice_root = (project.get("voice_root") or "").strip()
    video_root = (project.get("video_root") or "").strip()

    plan: list[dict[str, str]] = []
    if video_root:
        plan.append({"path": str(Path(video_root) / category), "purpose": "Roll-B 视频（按品类，不分配音员）"})
    if account and image_root:
        for t in templates:
            plan.append({"path": str(Path(image_root) / category / account / t), "purpose": f"商品图（{account} / {t}）"})
    if account and voice_root:
        plan.append({"path": str(Path(voice_root) / category / account), "purpose": f"配音文件（{account}）"})

    created: list[dict[str, str]] = []
    existed: list[dict[str, str]] = []
    for entry in plan:
        p = Path(entry["path"])
        if p.exists():
            existed.append(entry)
        else:
            p.mkdir(parents=True, exist_ok=True)
            created.append(entry)

    _json_out({
        "ok": True,
        "category": category,
        "account": account or None,
        "templates": templates,
        "created": created,
        "existed": existed,
        "account_dirs_pending": not account,
        "warning": template_warning or None,
    })


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
    _, _, _, wf = _init()
    result = wf.prepare_product_recommendation_output(
        project_id=args.project_id,
        account_label=args.account,
        output_mode=args.output_mode,
        product_media_mode=args.product_media_mode,
        stale_product_image_policy=getattr(args, "stale_product_image_policy", "block"),
        mode=args.mode,
        top_uids=args.top_uids,
        package_output_path=args.output or None,
    )
    _json_out(result)


def cmd_product_images(args: argparse.Namespace) -> None:
    _, _, _, wf = _init()
    result = wf.regenerate_product_card_images(
        project_id=args.project_id,
        account_label=args.account,
        mode=args.mode,
        product_uid=args.product_uid or "",
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

    # status
    p = sub.add_parser("status", help="项目状态概览")
    p.add_argument("project_id", type=int)

    # sync
    p = sub.add_parser("sync", help="同步 Master / MD / 素材")
    p.add_argument("project_id", type=int)
    p.add_argument("--step", choices=["master", "markdown", "assets"])
    p.add_argument("--asset-type", choices=["image", "video", "voice"])

    # voice
    p = sub.add_parser("voice", help="批量生成配音（MiniMax）")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音账户标签（如 小博）")
    p.add_argument("--uids", help="指定商品 UID，逗号分隔")

    # voice-counts
    p = sub.add_parser("voice-counts", help="配音生成数量预览")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音账户标签")

    # assemble
    p = sub.add_parser("assemble", help="组合口播稿")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音账户标签")
    p.add_argument("--intro-index", type=int, default=1, help="引言版本号（1-based）")
    p.add_argument("--output", "-o", help="口播稿输出路径")
    p.add_argument("--display-template", default="")

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

    # intro-plan
    p = sub.add_parser("intro-plan", help="用 CutMe 引言模板槽位生成文案和 intro_plan")
    p.add_argument("project_id", type=int)
    p.add_argument("--slots", required=True, help="引言槽位 JSON 文件")
    p.add_argument("--template", default="pain_avoidance_priority_v1", help="CutMe 引言模板 ID")
    p.add_argument("--label", default="引言1", help="写入 Markdown 的引言版本标题")
    p.add_argument("--markdown", help="覆盖写入目标 MD；默认使用项目 md_path 或文案骨架默认路径")
    p.add_argument("--sync", action="store_true", help="写入 Markdown 后立即同步入库")

    # scaffold
    p = sub.add_parser("scaffold", help="预建素材目录骨架（商品图/配音/Roll-B）")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", help="配音员（如 小歪）；不传则只建 Roll-B 目录")
    p.add_argument("--templates", help="覆盖商品图模板子目录（逗号分隔）；默认按配音员在 template_config 的坐标模板自动取")

    # assets-check
    p = sub.add_parser("assets-check", help="素材完整性检查")
    p.add_argument("project_id", type=int)

    p = sub.add_parser("render-package", help="Generate Remotion RenderPackage")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True)
    p.add_argument(
        "--output-mode",
        choices=["jianying_draft", "final_mp4"],
        default="jianying_draft",
    )
    p.add_argument(
        "--product-media-mode",
        choices=["cover_only", "video_preferred"],
        default="video_preferred",
        help="product display media: cover_only uses only the cover image; video_preferred uses product video when available",
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
    p.add_argument("--output", "-o", help="render-package.json output path")

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

    p = sub.add_parser("template-calibrate", help="生成单商品剪映模板位置校准草稿")
    p.add_argument("project_id", type=int)
    p.add_argument("--account", required=True, help="账号/用户标签，如 小燃")
    p.add_argument("--product-uid", required=True, help="用于校准的商品 UID")
    p.add_argument("--draft-name", default="", help="校准草稿名称")
    p.add_argument("--draft-root", default="", help="剪映草稿根目录")
    p.add_argument(
        "--product-media-mode",
        choices=["video_preferred"],
        default="video_preferred",
        help="模板校准必须使用商品视频模式",
    )

    return parser


DISPATCH = {
    "projects": cmd_projects,
    "status": cmd_status,
    "sync": cmd_sync,
    "voice": cmd_voice,
    "voice-counts": cmd_voice_counts,
    "assemble": cmd_assemble,
    "jianying": cmd_jianying,
    "outline": cmd_outline,
    "intro-plan": cmd_intro_plan,
    "scaffold": cmd_scaffold,
    "assets-check": cmd_assets_check,
    "render-package": cmd_render_package,
    "product-images": cmd_product_images,
    "template-calibrate": cmd_template_calibrate,
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
    except (ValueError, FileNotFoundError) as exc:
        _json_err(str(exc))
    except Exception:
        _json_err(traceback.format_exc())


if __name__ == "__main__":
    main()
