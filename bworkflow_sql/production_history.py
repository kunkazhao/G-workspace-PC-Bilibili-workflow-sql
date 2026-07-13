from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .repositories import Repository
from .settings import DEFAULT_PUBLISHED_VIDEO_ROOT
from .template_config import (
    available_templates,
    display_template_for_product_card_template_id,
    image_set_for_template,
    resolve_product_card_template,
)
from .utils import now_iso, safe_text
from .production_recipe import sha256_file, validate_production_recipe
from .cutme_adapter import CutMeAdapter
from .settings import INTERNAL_WORKSPACE_ROOT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_published_archive_dir(
    root: str | Path = DEFAULT_PUBLISHED_VIDEO_ROOT,
    *,
    now: datetime | None = None,
) -> Path:
    published_root = Path(root).expanduser().resolve()
    month_dir = published_root / f"{(now or datetime.now()).month}月"
    return month_dir if month_dir.is_dir() else published_root


class ProductionHistoryService:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def confirm(
        self,
        project_id: int,
        *,
        run_manifest_path: str | Path,
        final_path: str | Path | None = None,
    ) -> dict[str, Any]:
        project = self.repository.project(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")
        manifest_path = Path(run_manifest_path).expanduser().resolve()
        if not manifest_path.is_file():
            raise ValueError(f"运行清单不存在: {manifest_path}")
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if payload.get("kind") != "bworkflow.final_video_run":
            raise ValueError("运行清单不是完整 MP4 生成证据")
        manifest_project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
        if int(manifest_project.get("id") or 0) != project_id:
            raise ValueError("运行清单与项目不一致")
        account = safe_text(manifest_project.get("account"))
        selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        template_id = safe_text(selection.get("product_card_template_id"))
        if not account or not template_id:
            raise ValueError("运行清单缺少账号或商品卡模板")
        if safe_text(selection.get("acceptance_mode")) == "none":
            raise ValueError("未执行验收的 MP4 不能确认为正式成片")
        reports = payload.get("reports") if isinstance(payload.get("reports"), dict) else {}
        verification = reports.get("verification") if isinstance(reports.get("verification"), dict) else {}
        if not (verification.get("full_ffprobe") or verification.get("ffprobe")):
            raise ValueError("运行清单缺少完整 MP4 验收报告")
        outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
        generated_full_mp4_path = safe_text(outputs.get("full_mp4"))
        full_mp4 = Path(final_path or generated_full_mp4_path).expanduser()
        if not full_mp4.is_file():
            raise ValueError(f"完整 MP4 不存在: {full_mp4}")
        if full_mp4.suffix.lower() != ".mp4":
            raise ValueError("正式成片必须是 MP4 文件")
        metadata = resolve_product_card_template(account, template_id)
        display_name = safe_text(metadata.get("displayName")) or display_template_for_product_card_template_id(template_id)
        confirmed_at = now_iso()
        record = {
            "project_id": project_id,
            "category_name": safe_text(project.get("name")),
            "scheme_id": safe_text(project.get("scheme_id")),
            "scheme_name": safe_text(project.get("scheme_name")),
            "account_label": account,
            "template_id": template_id,
            "template_display_name": display_name,
            "template_dir": image_set_for_template(display_name),
            "run_manifest_path": str(manifest_path),
            "original_full_mp4_path": generated_full_mp4_path or str(full_mp4.resolve()),
            "full_mp4_path": str(full_mp4.resolve()),
            "full_mp4_sha256": _sha256(full_mp4),
            "full_mp4_size": full_mp4.stat().st_size,
            "acceptance_mode": safe_text(selection.get("acceptance_mode")),
            "generated_at": safe_text(payload.get("createdAt")),
            "confirmed_at": confirmed_at,
            "publish_status": "confirmed",
        }
        recipe_ref = payload.get("recipe") if isinstance(payload.get("recipe"), dict) else {}
        recipe_path = safe_text(recipe_ref.get("path"))
        recipe_hash = safe_text(recipe_ref.get("sha256"))
        generated_hash = next(
            (
                safe_text(item.get("sha256"))
                for item in payload.get("file_fingerprints") or []
                if isinstance(item, dict) and item.get("role") == "full_mp4"
            ),
            "",
        )
        actual_hash = record["full_mp4_sha256"]
        if generated_hash and generated_hash != actual_hash:
            recipe_status = "external_edit"
        elif recipe_path:
            recipe_status = validate_production_recipe(recipe_path, expected_sha256=recipe_hash)["recipe_status"]
        else:
            recipe_status = "legacy_unknown"
        record.update(
            {
                "recipe_path": recipe_path,
                "recipe_sha256": recipe_hash,
                "recipe_status": recipe_status,
                "supersedes_production_run_id": int(payload.get("rerenderedFromProductionRunId") or 0) or None,
            }
        )
        stored, created = self.repository.confirm_production_run(record)
        return {"ok": True, "created": created, "production": stored}

    def history(self, project_id: int, *, account_label: str) -> dict[str, Any]:
        project = self.repository.project(project_id)
        if not project:
            raise ValueError(f"项目不存在: {project_id}")
        account = safe_text(account_label)
        rows = self.repository.production_runs(
            project_id,
            account_label=account,
            category_name=safe_text(project.get("name")),
        )
        used_ids = list(dict.fromkeys(safe_text(row.get("template_id")) for row in rows if safe_text(row.get("template_id"))))
        options: list[dict[str, str]] = []
        for display_name in available_templates(account):
            try:
                metadata = resolve_product_card_template(account, display_name)
            except ValueError:
                # Jianying-only legacy templates are directory contracts, not
                # selectable Remotion product-card templates.
                continue
            template_id = safe_text(metadata.get("templateId"))
            if template_id and template_id not in {item["id"] for item in options}:
                options.append({"id": template_id, "display_name": safe_text(metadata.get("displayName")), "template_dir": image_set_for_template(safe_text(metadata.get("displayName")))})
        unused = [item for item in options if item["id"] not in used_ids]
        return {
            "ok": True,
            "project_id": project_id,
            "category": safe_text(project.get("name")),
            "account": account,
            "history": rows,
            "used_template_ids": used_ids,
            "unused_templates": unused,
            "recommended_template": unused[0] if unused else None,
        }

    def rerender_preflight(self, production_run_id: int) -> dict[str, Any]:
        record = self.repository.production_run(production_run_id)
        if not record:
            raise ValueError(f"正式成片记录不存在: {production_run_id}")
        status = safe_text(record.get("recipe_status")) or "legacy_unknown"
        if status != "reproducible":
            return {
                "ok": False,
                "rerenderable": False,
                "production_run_id": production_run_id,
                "recipe_status": status,
                "blocked_by": [{"code": status, "message": "该正式版本没有可直接重渲染的冻结配方"}],
            }
        validation = validate_production_recipe(
            safe_text(record.get("recipe_path")),
            expected_sha256=safe_text(record.get("recipe_sha256")),
        )
        source_bytes = sum(
            int(item.get("size") or 0)
            for item in (validation.get("recipe") or {}).get("sourceFiles", [])
            if isinstance(item, dict)
        )
        return {
            **{key: value for key, value in validation.items() if key != "recipe"},
            "production_run_id": production_run_id,
            "estimated_work": {
                "source_files": validation.get("source_count", 0),
                "source_bytes": source_bytes,
                "render_passes": 1,
            },
        }

    def rerender(
        self,
        production_run_id: int,
        *,
        pipeline_path: str | Path,
        delivery_dir: str | Path | None = None,
        cutme_adapter: CutMeAdapter | None = None,
    ) -> dict[str, Any]:
        preflight = self.rerender_preflight(production_run_id)
        if not preflight.get("rerenderable"):
            raise ValueError(f"重渲染预检失败: {preflight.get('blocked_by')}")
        record = self.repository.production_run(production_run_id)
        assert record is not None
        recipe_path = Path(safe_text(record.get("recipe_path"))).resolve()
        recipe = json.loads(recipe_path.read_text(encoding="utf-8-sig"))
        pipeline = Path(pipeline_path).expanduser().resolve()
        pipeline_payload = json.loads(pipeline.read_text(encoding="utf-8-sig"))
        configured_delivery = safe_text(pipeline_payload.get("output_dir"))
        if not delivery_dir and not configured_delivery:
            raise ValueError("重渲染缺少正式交付目录")
        target_root = (
            Path(delivery_dir).expanduser().resolve()
            if delivery_dir
            else Path(configured_delivery).expanduser().resolve()
        )
        target_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_root = INTERNAL_WORKSPACE_ROOT / f"project-{record['project_id']}" / "runs" / "artifacts" / timestamp
        process_dir = run_root / "process"
        process_dir.mkdir(parents=True, exist_ok=True)
        package_path = process_dir / "frozen-render-package.json"
        package_path.write_text(json.dumps(recipe["renderPackage"], ensure_ascii=False, indent=2), encoding="utf-8")
        candidate_dir = process_dir
        if process_dir.anchor.casefold() != target_root.anchor.casefold():
            candidate_dir = target_root.parent / ".bworkflow-staging"
            candidate_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = candidate_dir / f"complete-candidate-{timestamp}.partial.mp4"
        result = (cutme_adapter or CutMeAdapter()).render_final(package_path, output_path=candidate_path)
        rendered = Path(result["artifacts"]["output_path"]).resolve()
        verification = _probe_video(rendered)
        if verification["duration"] <= 0 or not verification["has_video"] or not verification["has_audio"]:
            raise ValueError("重渲染候选片未通过音视频完整性检查")

        final_path = target_root / f"完整成片-{timestamp}.mp4"
        rendered.replace(final_path)
        if candidate_dir != process_dir:
            try:
                candidate_dir.rmdir()
            except OSError:
                pass
        final_hash = sha256_file(final_path)

        original_manifest = json.loads(Path(record["run_manifest_path"]).read_text(encoding="utf-8-sig"))
        manifest_path = INTERNAL_WORKSPACE_ROOT / f"project-{record['project_id']}" / "runs" / f"final-video-{timestamp}.run-manifest.json"
        manifest = {
            **original_manifest,
            "createdAt": now_iso(),
            "rerenderedFromProductionRunId": production_run_id,
            "inputs": {**(original_manifest.get("inputs") or {}), "render_package_path": str(package_path)},
            "outputs": {**(original_manifest.get("outputs") or {}), "product_mp4": str(final_path), "full_mp4": str(final_path)},
            "recipe": {"path": str(recipe_path), "sha256": record["recipe_sha256"], "status": "reproducible"},
            "file_fingerprints": [
                {"role": "full_mp4", "path": str(final_path), "exists": True, "size": final_path.stat().st_size, "sha256": final_hash}
            ],
            "reports": {**(original_manifest.get("reports") or {}), "verification": {"full_ffprobe": verification}},
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _record_pending_candidate(
            pipeline,
            pipeline_payload,
            final_path=final_path,
            final_hash=final_hash,
            run_manifest_path=manifest_path,
            created_at=now_iso(),
        )
        return {
            "ok": True,
            "status": "candidate_generated",
            "production_run_id": production_run_id,
            "candidate_mp4": str(final_path),
            "run_manifest_path": str(manifest_path),
            "pipeline_path": str(pipeline),
            "verification": verification,
        }

    def complete_publishing(
        self,
        production_run_id: int,
        *,
        pipeline_path: str | Path,
        archive_dir: str | Path | None = None,
        current_path: str | Path | None = None,
        published_root: str | Path = DEFAULT_PUBLISHED_VIDEO_ROOT,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if archive_dir and current_path:
            raise ValueError("--archive-dir 和 --current-path 不能同时指定")
        if not archive_dir and not current_path:
            archive_dir = default_published_archive_dir(published_root, now=now)
        record = self.repository.production_run(production_run_id)
        if not record:
            raise ValueError(f"正式成片记录不存在: {production_run_id}")
        expected_hash = safe_text(record.get("full_mp4_sha256"))
        expected_size = int(record.get("full_mp4_size") or 0)
        source = Path(safe_text(record.get("full_mp4_path"))).expanduser().resolve()
        moved = False
        if archive_dir:
            if not source.is_file():
                raise ValueError(f"当前成片不存在，无法自动归档: {source}")
            _validate_video_identity(source, expected_hash=expected_hash, expected_size=expected_size)
            target_dir = Path(archive_dir).expanduser().resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            if target != source:
                if target.exists() and _sha256(target) != (expected_hash or _sha256(source)):
                    raise ValueError(f"归档目标存在不同文件: {target}")
                if not target.exists():
                    shutil.move(str(source), str(target))
                    moved = True
        else:
            target = Path(current_path).expanduser().resolve()
        if not target.is_file():
            raise ValueError(f"归档成片不存在: {target}")
        _validate_video_identity(target, expected_hash=expected_hash, expected_size=expected_size)
        timestamp = safe_text(record.get("archived_at")) or now_iso()
        stored = self.repository.mark_production_published(
            production_run_id,
            current_path=str(target),
            published_at=timestamp,
            archived_at=timestamp,
        )
        pipeline = Path(pipeline_path).expanduser().resolve()
        payload = json.loads(pipeline.read_text(encoding="utf-8-sig"))
        phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
        assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
        assembly["final_mp4_path"] = str(target)
        assembly["full_mp4_path"] = str(target)
        phases["assembly"] = assembly
        phases["publishing"] = {
            "status": "done",
            "production_run_id": production_run_id,
            "published_at": timestamp,
            "archive_status": "archived",
            "current_mp4_path": str(target),
        }
        paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
        paths["final_mp4"] = str(target)
        paths["full_mp4"] = str(target)
        payload["phases"] = phases
        payload["paths"] = paths
        payload["current_phase"] = "done"
        payload["next_action"] = "视频已发布并归档，流程完成。"
        payload["updated_at"] = timestamp
        pipeline.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "moved": moved, "production": stored, "pipeline_path": str(pipeline)}


def _probe_video(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"ffprobe failed: {completed.stderr[-500:]}")
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    return {
        "duration": float((payload.get("format") or {}).get("duration") or 0),
        "size": int((payload.get("format") or {}).get("size") or 0),
        "has_video": any(item.get("codec_type") == "video" for item in streams),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


def _validate_video_identity(path: Path, *, expected_hash: str, expected_size: int) -> None:
    if expected_size and path.stat().st_size != expected_size:
        raise ValueError("归档成片大小与正式成片记录不一致")
    if expected_hash and _sha256(path) != expected_hash:
        raise ValueError("归档成片 SHA-256 与正式成片记录不一致")


def _record_pending_candidate(
    pipeline_path: Path,
    payload: dict[str, Any],
    *,
    final_path: Path,
    final_hash: str,
    run_manifest_path: Path,
    created_at: str,
) -> None:
    phases = payload.get("phases") if isinstance(payload.get("phases"), dict) else {}
    assembly = phases.get("assembly") if isinstance(phases.get("assembly"), dict) else {}
    previous = assembly.get("pending_candidate") if isinstance(assembly.get("pending_candidate"), dict) else {}
    previous_path = Path(safe_text(previous.get("mp4_path"))).expanduser()
    previous_hash = safe_text(previous.get("sha256"))
    warning = ""
    if previous_path.is_file() and previous_path.resolve() != final_path.resolve():
        if previous_hash and sha256_file(previous_path) == previous_hash:
            project_id = safe_text((payload.get("project") or {}).get("id")) or "unknown"
            retired_dir = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "runs" / "superseded"
            retired_dir.mkdir(parents=True, exist_ok=True)
            previous_path.replace(retired_dir / previous_path.name)
        else:
            warning = "previous_candidate_changed_by_user_and_left_in_place"
    assembly["pending_candidate"] = {
        "status": "generated",
        "mp4_path": str(final_path),
        "sha256": final_hash,
        "run_manifest_path": str(run_manifest_path),
        "created_at": created_at,
        "warning": warning or None,
    }
    phases["assembly"] = assembly
    payload["phases"] = phases
    payload["updated_at"] = created_at
    pipeline_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
