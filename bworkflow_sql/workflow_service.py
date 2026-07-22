from __future__ import annotations

import json
import locale
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .asset_paths import voice_user_dir
from .content_constants import DEFAULT_CLOSING_TEXT
from .cutme_intro import (
    default_intro_plan_workspace,
    find_intro_plan_for_text,
    prepare_cutme_intro,
    preflight_intro_plan_for_cutme,
    run_cutme_render,
)
from .db import Database
from .repositories import Repository
from .resource_lifecycle import (
    DERIVED_ASSET_RETENTION_DAYS,
    record_resource_state_event_in_connection,
    register_cleanup_candidate_in_connection,
)
from .settings import (
    CUTME_ROOT,
    DEFAULT_INDEXTTS_DIR,
    DEFAULT_INTRO_ASSET_ROOT,
    DEFAULT_JIANYING_DRAFT_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_STANDALONE_VOICE_ROOT,
    DEFAULT_TTS_API_BASE_URL,
    INTERNAL_WORKSPACE_ROOT,
    JIANYING_ENGINE_DIR,
    LEGACY_B_WORKFLOW_SKILL_SCRIPTS,
)
from .utils import file_metadata, now_iso, safe_text, text_hash
from .workflow_errors import (
    AmbiguousProjectReferenceError,
    InvalidWorkflowRequestError,
    ProjectNotFoundError,
)
from .template_config import (
    display_template_from_image_path,
    display_template_for_product_card_template_id,
    image_set_for_template,
    product_card_text_capacity_certification_issues,
    resolve_product_card_template,
    user_for_template,
)
from .tts_adapters import IndexTtsProvider, MiniMaxTtsProvider
from .tts_contracts import TtsProviderRegistry, TtsSynthesisRequest, VoiceSynthesisIdentity
from .tts_helpers import (  # noqa: F401 – re-exported
    DEFAULT_KEEP_SILENCE_MS,
    DEFAULT_LEADING_SILENCE_MS,
    DEFAULT_LONG_SILENCE_KEEP_MS,
    DEFAULT_LONG_SILENCE_MS,
    DEFAULT_MAX_LEADING_SILENCE_MS,
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_SILENCE_CHUNK_MS,
    DEFAULT_SILENCE_THRESHOLD_DB,
    DEFAULT_TRAILING_SILENCE_KEEP_MS,
    DEFAULT_TRAILING_SILENCE_LIMIT_MS,
    DEFAULT_TTS_FIELDS,
    MINIMAX_API_BASE_URL,
    MINIMAX_KNOWN_LOCAL_VOICE_IDS,
    MINIMAX_SKILL_ENV_PATH,
    MINIMAX_T2A_MODEL,
    MINIMAX_T2A_URL,
    MINIMAX_VOICE_ALIASES,
    MINIMAX_VOICE_LIST_URL,
    VOICE_PROVIDER_INDEXTTS,
    VOICE_PROVIDER_LABELS,
    VOICE_PROVIDER_MINIMAX,
    account_voice_id_for_provider,
    compress_internal_silence,
    default_voice_model,
    default_voice_synthesis_settings,
    dbfs_for_chunk,
    load_minimax_api_key,
    markdown_file_to_voice_text,
    markdown_to_voice_text,
    normalize_audio_loudness,
    normalize_generated_voice_silence,
    normalize_voice_provider,
    prepend_silence,
    resolve_minimax_voice_id,
    seconds_to_frames,
    silence_ranges_for_audio,
    voice_provider_label,
    voice_synthesis_identity,
)
from .subtitle_helpers import (  # noqa: F401 – re-exported
    DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    DEFAULT_SUBTITLE_ASR_LANGUAGE,
    DEFAULT_SUBTITLE_ASR_MODEL,
    DEFAULT_SUBTITLE_ASR_WORKERS,
    DEFAULT_SUBTITLE_OVERLAP_GAP_SEC,
    DEFAULT_SUBTITLE_SPEECH_SNAP_WINDOW_SEC,
    SUBTITLE_ALIGN_DROP_RE,
    SUBTITLE_BREAK_RE,
    SUBTITLE_DROP_PUNCT_RE,
    align_subtitle_jobs_with_asr,
    align_subtitle_text_with_asr,
    distribute_subtitle_text,
    format_srt,
    format_srt_timestamp,
    normalize_subtitle_alignment_text,
    probe_media_duration_seconds,
    split_subtitle_text,
    subtitle_asr_python_path,
    subtitle_entry_label,
    subtitle_manifest_entries,
)
from .draft_helpers import (  # noqa: F401 – re-exported
    format_duration_cn,
    format_jianying_run_stdout,
    parse_json_object,
)


from .render_package_builder import (
    DEFAULT_PRODUCT_MEDIA_MODE,
    DEFAULT_PRODUCT_ORDER_STRATEGY,
    SUPPORTED_PRODUCT_MEDIA_MODES,
    SUPPORTED_PRODUCT_ORDER_STRATEGIES,
    SUPPORTED_OUTPUT_MODES,
    SUPPORTED_SUBTITLE_ALIGNMENTS,
    build_product_recommendation_package,
    _arrange_price_segment_shuffle,
    _has_matching_price_groups,
    product_card_payload_for_product,
)
from .product_image_generation import regenerate_product_card_images
from .template_doctor import diagnose_template_flow
from .script_doctor import diagnose_script_flow
from .episode_materializer import materialize_episode_markdown
from .markdown_paths import ensure_markdown_write_target


INTERNAL_PREFIX = "internal:"
DEFAULT_DISPLAY_VIDEO_SLOT = {
    "x": 1100,
    "y": 178,
    "width": 410,
    "height": 258,
}
@dataclass
class WorkflowRunResult:
    args: list[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _product_card_text_capacity_issues(
    *,
    account_label: str,
    product_card_template_id: str,
) -> list[dict[str, Any]]:
    if not safe_text(product_card_template_id):
        return []
    metadata = resolve_product_card_template(
        account_label,
        product_card_template_id,
        require_explicit=True,
    )
    return product_card_text_capacity_certification_issues(metadata)


@dataclass
class VoiceJob:
    block: dict[str, Any]
    uid: str
    product_name: str
    price_label: str = ""
    index: int = 0
    kind: str = "product"
    price_range_label: str = ""


ROLL_B_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi"}


@dataclass
class RollBRenameItem:
    source_path: str
    target_path: str
    source_name: str
    target_name: str
    uid: str
    title: str
    price_label: str
    status: str
    message: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "source_path": self.source_path,
            "target_path": self.target_path,
            "source_name": self.source_name,
            "target_name": self.target_name,
            "uid": self.uid,
            "title": self.title,
            "price_label": self.price_label,
            "status": self.status,
            "message": self.message,
        }




class WorkflowService:
    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)
        self._tts_log_handles: list[Any] = []

    def export_project_markdown(self, project_id: int, target_path: str | Path | None = None) -> Path:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        products = self.repo.products(project_id, include_removed=False)
        blocks = self.repo.script_blocks(project_id)
        assets = self.repo.asset_bindings(project_id)
        target = Path(target_path) if target_path else self._internal_project_dir(project_id) / "project-export.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        by_type: dict[str, list[dict[str, Any]]] = {"intro": [], "product": [], "price_transition": []}
        for block in blocks:
            by_type.setdefault(block["script_type"], []).append(block)
        asset_paths: dict[tuple[str, str], str] = {}
        for asset in assets:
            if asset["status"] != "ready":
                continue
            key = (asset["uid"], asset["asset_type"])
            asset_paths.setdefault(key, safe_text(asset.get("path")))
        lines: list[str] = []
        lines += ["## 引言文案", ""]
        for block in by_type.get("intro", []):
            script_id = safe_text(block.get("script_id")) or f"script-{block['id']}"
            lines += [f"<!-- script_id: {script_id} -->", f"### {block['block_label']}", block["body"], ""]
        lines += ["## 商品文案", ""]
        product_blocks: dict[str, list[dict[str, Any]]] = {}
        for block in by_type.get("product", []):
            product_blocks.setdefault(block["owner_uid"], []).append(block)
        for product in products:
            lines += [f"### {product['price_label']}-{product['uid']}-{product['title']}", ""]
            for block in product_blocks.get(product["uid"], []):
                script_id = safe_text(block.get("script_id")) or f"script-{block['id']}"
                lines += [f"<!-- script_id: {script_id} -->", f"#### {block['block_label']}", block["body"], ""]
            lines += [
                f"图片：{asset_paths.get((product['uid'], 'image'), '')}",
                f"视频：{asset_paths.get((product['uid'], 'video'), '')}",
                "",
            ]
        lines += ["## 价格过渡文案", ""]
        price_groups: dict[str, list[dict[str, Any]]] = {}
        for block in by_type.get("price_transition", []):
            price_groups.setdefault(block["price_range_label"], []).append(block)
        for label, group in price_groups.items():
            lines += [f"### {label}", ""]
            for block in group:
                script_id = safe_text(block.get("script_id")) or f"script-{block['id']}"
                lines += [f"<!-- script_id: {script_id} -->", f"#### {block['block_label']}", block["body"], ""]
        lines += ["## 商品顺序", ""]
        for index, product in enumerate(products, start=1):
            lines.append(f"{index}. {product['uid']} {product['title']}")
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return target

    def build_voice_command(
        self,
        project_id: int,
        account_label: str = "",
        uids: list[str] | None = None,
        script_ids: list[str] | None = None,
        voice_provider: str = VOICE_PROVIDER_INDEXTTS,
    ) -> list[str]:
        cmd = [f"{INTERNAL_PREFIX}voice", "--project-id", str(project_id)]
        provider = normalize_voice_provider(voice_provider)
        if provider != VOICE_PROVIDER_INDEXTTS:
            cmd += ["--voice-provider", provider]
        if account_label:
            cmd += ["--account-label", account_label]
        if uids:
            cmd += ["--uids", ",".join(uids)]
        if script_ids:
            cmd += ["--script-ids", ",".join(script_ids)]
        return cmd

    def build_assembly_command(
        self,
        project_id: int,
        *,
        mode: str = "standard",
        top_uids: list[str] | None = None,
        product_order_strategy: str = DEFAULT_PRODUCT_ORDER_STRATEGY,
        account_label: str = "",
        intro_index: int = 1,
        product_uids: list[str] | None = None,
        output_markdown_path: str | Path | None = None,
        display_template: str = "",
    ) -> list[str]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        output_markdown = self._spoken_markdown_path(project, output_markdown_path)
        cmd = [
            f"{INTERNAL_PREFIX}assembly",
            "--project-id",
            str(project_id),
            "--mode",
            "top" if mode == "top" else "standard",
            "--product-order-strategy",
            safe_text(product_order_strategy) or DEFAULT_PRODUCT_ORDER_STRATEGY,
            "--intro-index",
            str(max(1, int(intro_index or 1))),
            "--output-markdown",
            str(output_markdown),
        ]
        if account_label:
            cmd += ["--account-label", account_label]
        if product_uids:
            cmd += ["--uids", ",".join(product_uids)]
        if top_uids:
            cmd += ["--top-uids", ",".join(top_uids)]
        if display_template:
            cmd += ["--display-template", display_template]
        return cmd

    def build_jianying_command(
        self,
        project_id: int,
        *,
        draft_name: str = "",
        spoken_markdown_path: str | Path | None = None,
        intro_video_path: str | Path | None = None,
    ) -> list[str]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        output_markdown = self._spoken_markdown_path(project, spoken_markdown_path)
        manifest = self.spoken_manifest_path(project_id, output_markdown)
        cmd = [
            f"{INTERNAL_PREFIX}jianying",
            "--project-id",
            str(project_id),
            "--manifest",
            str(manifest),
            "--draft-name",
            safe_path_component(draft_name or safe_text(project.get("name")) or "B-Workflow-SQL"),
            "--draft-root",
            str(DEFAULT_JIANYING_DRAFT_ROOT),
        ]
        intro_video = safe_text(intro_video_path)
        if intro_video:
            cmd += ["--intro-video", intro_video]
        return cmd

    def prepare_product_recommendation_output(
        self,
        project_id: int,
        *,
        account_label: str,
        output_mode: str,
        product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
        product_order_strategy: str = DEFAULT_PRODUCT_ORDER_STRATEGY,
        stale_product_image_policy: str = "block",
        mode: str = "standard",
        top_uids: str | list[str] | None = None,
        product_uids: str | list[str] | None = None,
        product_card_template_id: str = "",
        package_output_path: str | Path | None = None,
        subtitle_alignment: str = "proportional",
        intro_video_path: str | Path | None = None,
        intro_video_text: str = "",
        include_outro: bool = False,
        closing_text: str = "",
    ) -> dict[str, Any]:
        output_mode_value = safe_text(output_mode) or "jianying_draft"
        if output_mode_value not in SUPPORTED_OUTPUT_MODES:
            raise ValueError(f"unsupported output_mode: {output_mode_value}")
        media_mode = safe_text(product_media_mode) or DEFAULT_PRODUCT_MEDIA_MODE
        if media_mode not in SUPPORTED_PRODUCT_MEDIA_MODES:
            raise ValueError(f"unsupported product_media_mode: {media_mode}")
        order_strategy = safe_text(product_order_strategy) or DEFAULT_PRODUCT_ORDER_STRATEGY
        if order_strategy not in SUPPORTED_PRODUCT_ORDER_STRATEGIES:
            raise ValueError(f"unsupported product_order_strategy: {order_strategy}")
        subtitle_mode = safe_text(subtitle_alignment) or "proportional"
        if subtitle_mode not in SUPPORTED_SUBTITLE_ALIGNMENTS:
            raise ValueError(f"unsupported subtitle_alignment: {subtitle_mode}")
        stale_policy = safe_text(stale_product_image_policy) or "block"
        if stale_policy not in {"block", "reuse"}:
            raise ValueError(f"unsupported stale_product_image_policy: {stale_policy}")
        sequence_mode = safe_text(mode) or "standard"
        top_uid_list = split_csv(top_uids) if isinstance(top_uids, str) else list(top_uids or [])
        product_uid_list = (
            split_csv(product_uids) if isinstance(product_uids, str) else list(product_uids or [])
        )

        certification_issues = _product_card_text_capacity_issues(
            account_label=account_label,
            product_card_template_id=product_card_template_id,
        )
        if certification_issues:
            return {
                "ok": False,
                "stage": "product_card_text_capacity_gate",
                "project_id": project_id,
                "account": account_label,
                "product_card_template_id": safe_text(product_card_template_id) or None,
                "product_media_mode": media_mode,
                "issues": certification_issues,
                "next": {"action": "run_product_card_text_capacity_gate"},
            }

        build_kwargs: dict[str, Any] = {}
        if intro_video_path:
            build_kwargs["intro_video_path"] = intro_video_path
            build_kwargs["intro_video_text"] = intro_video_text
        if include_outro:
            build_kwargs["include_outro"] = True
            build_kwargs["closing_text"] = closing_text

        result = build_product_recommendation_package(
            self.db,
            project_id=project_id,
            account_label=account_label,
            output_mode=output_mode_value,
            product_media_mode=media_mode,
            product_order_strategy=order_strategy,
            product_card_template_id=product_card_template_id,
            mode=sequence_mode,
            top_uids=top_uid_list,
            product_uids=product_uid_list,
            subtitle_alignment=subtitle_mode,
            **build_kwargs,
        )
        output_path = (
            Path(package_output_path)
            if package_output_path
            else INTERNAL_WORKSPACE_ROOT
            / f"project-{project_id}"
            / "render"
            / f"render-package-{safe_path_component(account_label)}-{output_mode_value}.json"
        )
        base_payload: dict[str, Any] = {
            "project_id": project_id,
            "account": account_label,
            "output_mode": output_mode_value,
            "product_media_mode": media_mode,
            "product_order_strategy": order_strategy,
            "product_card_template_id": safe_text(product_card_template_id) or None,
            "subtitle_alignment": subtitle_mode,
            "mode": sequence_mode,
            "top_uids": top_uid_list,
            "product_uids": product_uid_list,
            "package_path": str(output_path),
            "missing": result.missing,
            "stale_product_images": getattr(result, "stale_product_images", []),
        }
        if result.missing:
            return {"ok": False, **base_payload, "next": None}
        if base_payload["stale_product_images"] and stale_policy == "block":
            return {
                "ok": False,
                **base_payload,
                "next": render_package_stale_product_image_next_step(
                    base_payload["stale_product_images"],
                    project_id=project_id,
                    account_label=account_label,
                    product_card_template_id=product_card_template_id,
                ),
            }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result.package, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        jianying_manifest_path: Path | None = None
        if output_mode_value == "jianying_draft":
            jianying_manifest_path = output_path.with_suffix(".jianying.manifest.json")
            render_package_to_jianying_manifest(
                result.package,
                jianying_manifest_path,
                project_id=project_id,
                account_label=account_label,
            )
        return {
            "ok": True,
            **base_payload,
            "segment_counts": render_segment_counts(result.package.get("segments", [])),
            "next": render_package_next_step(
                project_id=project_id,
                account_label=account_label,
                output_mode=output_mode_value,
                package_path=output_path,
                jianying_manifest_path=jianying_manifest_path,
            ),
        }

    def regenerate_product_card_images(
        self,
        project_id: int,
        *,
        account_label: str,
        mode: str = "stale",
        product_uid: str = "",
        product_card_template_id: str = "",
        max_workers: int = 3,
    ) -> dict[str, Any]:
        certification_issues = _product_card_text_capacity_issues(
            account_label=account_label,
            product_card_template_id=product_card_template_id,
        )
        if certification_issues:
            return {
                "ok": False,
                "stage": "product_card_text_capacity_gate",
                "project_id": project_id,
                "account": account_label,
                "product_card_template_id": safe_text(product_card_template_id) or None,
                "issues": certification_issues,
                "next": {"action": "run_product_card_text_capacity_gate"},
            }
        return regenerate_product_card_images(
            self.db,
            project_id=project_id,
            account_label=account_label,
            mode=mode,
            product_uid=product_uid,
            product_card_template_id=product_card_template_id,
            max_workers=max_workers,
        )

    def template_doctor(
        self,
        project_id: int,
        *,
        account_label: str,
        product_card_template_id: str = "",
        product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
    ) -> dict[str, Any]:
        return diagnose_template_flow(
            self.db,
            project_id=project_id,
            account_label=account_label,
            product_card_template_id=product_card_template_id,
            product_media_mode=product_media_mode,
        )

    def product_card_preflight(
        self,
        project_id: int,
        *,
        account_label: str,
        product_card_template_id: str,
        product_uid: str = "",
        expect_cover: str = "",
    ) -> dict[str, Any]:
        from .product_card_preflight import product_card_preflight

        return product_card_preflight(
            self.db,
            project_id=project_id,
            account_label=account_label,
            product_card_template_id=product_card_template_id,
            product_uid=product_uid,
            expect_cover=expect_cover,
        )

    def script_doctor(
        self,
        project_id: int,
        *,
        intro_label: str = "",
    ) -> dict[str, Any]:
        return diagnose_script_flow(
            self.db,
            project_id=project_id,
            intro_label=intro_label,
        )

    def workflow_doctor(
        self,
        project_ref: int | str,
        *,
        account_label: str = "",
        scheme_name: str = "",
        intro_label: str = "",
        intro_index: int = 1,
        mode: str = "standard",
        top_uids: str | list[str] | None = None,
        product_order_strategy: str = DEFAULT_PRODUCT_ORDER_STRATEGY,
        product_card_template_id: str = "",
        product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
    ) -> dict[str, Any]:
        project_id = self.resolve_project_ref(project_ref, scheme_name=scheme_name)
        project = self._required_project(project_id)
        account = safe_text(account_label)
        top_uid_list = split_csv(top_uids) if isinstance(top_uids, str) else list(top_uids or [])
        checks: dict[str, Any] = {
            "phase7_selection": _phase7_selection_context(
                self.repo.products(project_id, include_removed=False)
            ),
            "script": None,
            "voice_and_assembly": None,
            "intro_preflight": None,
            "template": None,
        }
        issues: list[dict[str, Any]] = []

        script = self.script_doctor(project_id, intro_label=intro_label)
        checks["script"] = script
        issues.extend(_workflow_doctor_issues("script-doctor", script.get("issues") or []))
        if safe_text(script.get("status")) != "ready_for_downstream":
            return _workflow_doctor_payload(
                project_id=project_id,
                project=project,
                account_label=account,
                ok=False,
                status="blocked",
                blocked_by="script",
                checks=checks,
                issues=issues,
                next_hint=script.get("next") or {},
            )

        assembly = self.assemble_spoken_script_plan(
            project_id,
            account_label=account,
            intro_index=intro_index,
            mode=mode,
            top_uids=top_uid_list,
            product_order_strategy=product_order_strategy,
        )
        checks["voice_and_assembly"] = assembly
        issues.extend(_workflow_doctor_issues("assemble-plan", assembly.get("issues") or []))
        if safe_text(assembly.get("status")) != "ready_to_assemble":
            return _workflow_doctor_payload(
                project_id=project_id,
                project=project,
                account_label=account,
                ok=False,
                status="blocked",
                blocked_by="voice_and_assembly",
                checks=checks,
                issues=issues,
                next_hint=assembly.get("next") or {},
            )

        selected_intro = script.get("selected_intro") if isinstance(script.get("selected_intro"), dict) else {}
        source_intro_plan_path = safe_text(selected_intro.get("source_intro_plan_path"))
        if source_intro_plan_path:
            intro_preflight = preflight_intro_plan_for_cutme(
                source_plan_path=source_intro_plan_path,
                project=project,
            )
            checks["intro_preflight"] = intro_preflight
            issues.extend(_workflow_doctor_issues("intro-preflight", intro_preflight.get("issues") or []))
            if not bool(intro_preflight.get("ok")):
                return _workflow_doctor_payload(
                    project_id=project_id,
                    project=project,
                    account_label=account,
                    ok=False,
                    status="blocked",
                    blocked_by="intro_preflight",
                    checks=checks,
                    issues=issues,
                    next_hint=intro_preflight.get("next") or {},
                )

        if account and safe_text(product_card_template_id):
            template = self.template_doctor(
                project_id,
                account_label=account,
                product_card_template_id=product_card_template_id,
                product_media_mode=product_media_mode,
            )
            checks["template"] = template
            issues.extend(_workflow_doctor_issues("template-doctor", template.get("issues") or []))
            if not bool(template.get("ok")):
                return _workflow_doctor_payload(
                    project_id=project_id,
                    project=project,
                    account_label=account,
                    ok=False,
                    status="blocked",
                    blocked_by="template",
                    checks=checks,
                    issues=issues,
                    next_hint=template.get("next") or {},
                )

        return _workflow_doctor_payload(
            project_id=project_id,
            project=project,
            account_label=account,
            ok=True,
            status="ready",
            blocked_by="",
            checks=checks,
            issues=issues,
            next_hint=assembly.get("next") or {},
        )

    def render_intro_video(
        self,
        project_id: int,
        *,
        account_label: str,
        intro_label: str = "寮曟█1",
        output_path: str | Path | None = None,
        asset_root: str | Path = "",
        pipeline_path: str | Path | None = None,
    ) -> dict[str, Any]:
        project = self._required_project(project_id)
        account = safe_text(account_label)
        if not account:
            raise ValueError("render-intro-video requires --account")
        intro_block = self._intro_block_for_label(project_id, intro_label)
        voice = self._ready_asset(
            self.repo.asset_bindings(project_id),
            asset_type="voice",
            uid="INTRO",
            account_label=account,
            script_block_id=int(intro_block["id"]),
            text_hash=safe_text(intro_block.get("text_hash")),
            block_label=safe_text(intro_block.get("block_label")),
        )
        if not voice:
            raise ValueError("current intro block has no ready voice asset with matching text_hash")
        voice_path = Path(safe_text(voice.get("path"))).expanduser().resolve()
        if not voice_path.is_file():
            raise ValueError(f"intro voice asset does not exist: {voice_path}")
        source_plan_path = find_intro_plan_for_text(project_id, safe_text(intro_block.get("body")))
        if source_plan_path is None:
            raise ValueError("missing matching source-intro-plan for the selected intro block")
        if output_path or not pipeline_path:
            target = self._intro_video_output_path(
                project_id,
                account_label=account,
                intro_label=safe_text(intro_block.get("block_label")) or intro_label,
                output_path=output_path,
            )
        else:
            from .production_delivery import resolve_project_delivery_dir

            target = resolve_project_delivery_dir(
                project=project,
                account_label=account,
                pipeline_path=pipeline_path,
            ) / "引言视频.mp4"
        prepared = prepare_cutme_intro(
            source_plan_path=source_plan_path,
            audio_path=voice_path,
            project=project,
            account_label=account,
            script_block_id=int(intro_block["id"]),
            intro_text=safe_text(intro_block.get("body")),
            title=safe_text(project.get("name")) or "Bilibili Intro",
            asset_root=asset_root or DEFAULT_INTRO_ASSET_ROOT,
        )
        rendered = run_cutme_render(prepared.config_path, target)
        config = json.loads(prepared.config_path.read_text(encoding="utf-8-sig"))
        subtitles = config.get("subtitles") if isinstance(config.get("subtitles"), list) else []
        output_config = config.get("output") if isinstance(config.get("output"), dict) else {}
        subtitle_config = (
            output_config.get("subtitles")
            if isinstance(output_config.get("subtitles"), dict)
            else {}
        )
        report_path = prepared.intro_plan_path.with_suffix(".report.json")
        if pipeline_path:
            from .production_delivery import record_pipeline_video_path

            record_pipeline_video_path(pipeline_path, key="intro_video", video_path=rendered)
            pipeline_file = Path(pipeline_path).expanduser().resolve()
            pipeline = json.loads(pipeline_file.read_text(encoding="utf-8-sig"))
            phases = pipeline.get("phases") if isinstance(pipeline.get("phases"), dict) else {}
            intro_phase = phases.get("intro_video") if isinstance(phases.get("intro_video"), dict) else {}
            intro_phase.update(
                {
                    "status": "awaiting_user_review",
                    "accepted": False,
                    "output_mp4_path": str(Path(rendered).resolve()),
                    "updated_at": now_iso(),
                }
            )
            phases["intro_video"] = intro_phase
            pipeline["phases"] = phases
            pipeline["current_phase"] = "intro_video"
            pipeline["next_action"] = "请用户验收引言视频；验收通过后再进入组装。"
            pipeline["updated_at"] = now_iso()
            pipeline_file.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "project_id": project_id,
            "account": account,
            "intro_label": safe_text(intro_block.get("block_label")),
            "script_block_id": int(intro_block["id"]),
            "source_intro_plan_path": str(source_plan_path),
            "prepared_intro_plan_path": str(prepared.intro_plan_path),
            "cutme_config_path": str(prepared.config_path),
            "report_path": str(report_path),
            "output_path": str(rendered),
            "subtitle_enabled": subtitle_config.get("enabled") is True,
            "subtitle_count": len(subtitles) if subtitle_config.get("enabled") is True else 0,
            "selected_assets": prepared.selected_assets,
            "preflight": prepared.preflight,
            "aligned_with_asr": prepared.aligned_with_asr,
            "verification_hint": {
                "ffprobe": f"ffprobe -v error -show_streams -show_format {rendered}",
                "frames": f"ffmpeg -hide_banner -ss 3 -i {rendered} -frames:v 1 <frame-3s.png>",
            },
        }

    def resolve_project_ref(
        self,
        project_ref: int | str,
        *,
        scheme_name: str = "",
    ) -> int:
        if isinstance(project_ref, int):
            return project_ref
        ref_text = safe_text(project_ref)
        if not ref_text:
            raise InvalidWorkflowRequestError("project reference cannot be empty")
        if ref_text.isdigit():
            return int(ref_text)
        normalized_ref = _normalize_project_lookup_text(ref_text)
        normalized_scheme = _normalize_project_lookup_text(scheme_name)
        matches: list[dict[str, Any]] = []
        for project in self.repo.projects():
            candidates = [
                project.get("name"),
                project.get("category_name"),
                f"{safe_text(project.get('category_parent_name'))}-{safe_text(project.get('category_name'))}",
            ]
            if not any(_project_ref_matches(normalized_ref, candidate) for candidate in candidates):
                continue
            if normalized_scheme and not _project_ref_matches(normalized_scheme, project.get("scheme_name")):
                continue
            matches.append(project)
        if len(matches) == 1:
            return int(matches[0]["id"])
        if not matches:
            raise ProjectNotFoundError(f"project reference not found: {ref_text}")
        summary = "; ".join(
            f"id={item.get('id')} name={safe_text(item.get('name'))} scheme={safe_text(item.get('scheme_name'))}"
            for item in matches[:8]
        )
        raise AmbiguousProjectReferenceError(
            f"ambiguous project reference: {ref_text}. "
            f"Pass --scheme-name or use a numeric project_id. Matches: {summary}"
        )

    def materialize_episode_markdown(
        self,
        project_id: int,
        *,
        library_path: str | Path | None = None,
    ) -> dict[str, Any]:
        return materialize_episode_markdown(
            self.db,
            project_id=project_id,
            library_path=library_path,
        )

    def template_calibration_probe(
        self,
        project_id: int,
        *,
        account_label: str,
        product_uid: str,
        draft_name: str = "",
        draft_root: str | Path | None = None,
        product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
        product_card_template_id: str = "",
        stale_product_image_policy: str = "reuse",
    ) -> dict[str, Any]:
        account = safe_text(account_label)
        uid = safe_text(product_uid)
        if not account:
            raise ValueError("template calibration requires account_label")
        if not uid:
            raise ValueError("template calibration requires product_uid")

        selected_template = resolve_product_card_template(
            account,
            product_card_template_id,
            require_explicit=True,
        )
        selected_template_id = safe_text(selected_template.get("templateId")) or safe_text(product_card_template_id)
        output_dir = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}" / "template-calibration"
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"template-calibrate-{safe_path_component(account)}-{safe_path_component(uid)}"
        placeholder_audio = output_dir / "template-calibration-placeholder.wav"
        ensure_template_calibration_placeholder_audio(placeholder_audio)
        project = self._required_project(project_id)
        probe_payload = build_template_calibration_probe_manifest_from_assets(
            project=project,
            products=self.repo.products(project_id, include_removed=False),
            assets=self.repo.asset_bindings(project_id),
            account_label=account,
            product_uid=uid,
            product_media_mode=product_media_mode,
            product_card_template_id=selected_template_id,
            placeholder_audio_path=placeholder_audio,
        )
        probe_manifest = output_dir / f"{stem}.manifest.json"
        probe_manifest.write_text(
            json.dumps(probe_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        draft = self.generate_jianying_draft(
            project_id,
            manifest_path=probe_manifest,
            draft_name=safe_path_component(draft_name or f"template-calibration-{account}-{uid}"),
            draft_root=draft_root or DEFAULT_JIANYING_DRAFT_ROOT,
        )
        entry = probe_payload["entries"][0]
        return {
            "ok": draft.returncode == 0,
            "project_id": project_id,
            "account": account,
            "product_uid": uid,
            "product_card_template_id": selected_template_id,
            "display_template": safe_text(probe_payload.get("display_template")),
            "display_video_slot": entry.get("display_video_slot"),
            "probe_manifest_path": str(probe_manifest),
            "placeholder_audio_path": str(placeholder_audio),
            "draft": {
                "returncode": draft.returncode,
                "stdout": draft.stdout,
                "stderr": draft.stderr,
            },
        }
    def run_command(self, cmd: list[str]) -> WorkflowRunResult:
        if cmd and cmd[0].startswith(INTERNAL_PREFIX):
            return self._run_internal(cmd)
        return run_subprocess_text(cmd)

    def is_tts_service_running(self, timeout: float = 1.0) -> bool:
        return self._api_health(JsonHttpClient(timeout=timeout)) is not None

    def shutdown_tts_service(self) -> int:
        pids = self._find_tts_service_pids()
        if not pids:
            return 0
        killed = 0
        for pid in pids:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="gbk",
                errors="ignore",
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0:
                killed += 1
        for handle in self._tts_log_handles:
            try:
                handle.close()
            except Exception:
                pass
        self._tts_log_handles.clear()
        return killed

    def voice_generation_counts(
        self,
        project_id: int,
        *,
        account_label: str = "",
        voice_provider: str = VOICE_PROVIDER_INDEXTTS,
        uids: list[str] | None = None,
        script_ids: list[str] | None = None,
    ) -> tuple[int, int, int]:
        account = self._resolve_account(account_label)
        if not account:
            return 0, 0, 0
        provider = normalize_voice_provider(voice_provider)
        configuration = self._voice_configuration_for_account(account, provider)
        if not configuration:
            return 0, 0, 0
        identity, _settings = configuration
        jobs = self._voice_jobs(project_id, uids=uids, script_ids=script_ids)
        existing, pending = self._split_existing_voice_jobs(project_id, jobs, account, identity=identity)
        return len(jobs), len(existing), len(pending)

    def generate_voice(
        self,
        project_id: int,
        *,
        account_label: str = "",
        voice_provider: str = VOICE_PROVIDER_INDEXTTS,
        uids: list[str] | None = None,
        script_ids: list[str] | None = None,
        output_dir: str | Path | None = None,
        start_service_if_needed: bool = True,
        progress_hook: Callable[[str], None] | None = None,
        cancel_event: "threading.Event | None" = None,
    ) -> WorkflowRunResult:
        logs: list[str] = []
        def emit(message: str) -> None:
            logs.append(message)
            if progress_hook:
                progress_hook(message)

        project = self._required_project(project_id)
        account = self._resolve_account(account_label)
        if not account:
            raise ValueError("请先在用户管理里配置配音用户。")
        provider = normalize_voice_provider(voice_provider)
        configuration = self._voice_configuration_for_account(account, provider)
        if not configuration:
            missing_field = "MiniMax 音色标识" if provider == VOICE_PROVIDER_MINIMAX else "IndexTTS 音色标识"
            raise ValueError(f"用户“{account.get('label') or account_label}”缺少{missing_field}。请到用户管理里补齐。")
        identity, synthesis_settings = configuration
        out_dir = Path(output_dir) if safe_text(output_dir) else self._voice_output_dir(project, account=account, account_label=account_label)
        out_dir.mkdir(parents=True, exist_ok=True)
        jobs = self._voice_jobs(project_id, uids=uids, script_ids=script_ids)
        if not jobs:
            return WorkflowRunResult([f"{INTERNAL_PREFIX}voice"], stdout="没有需要生成配音的文案。\n")

        existing, pending = self._split_existing_voice_jobs(project_id, jobs, account, identity=identity)
        emit(f"[配音任务] 文案 {len(jobs)} 条，已存在 {len(existing)} 条，待生成 {len(pending)} 条。")
        if not pending:
            return WorkflowRunResult([f"{INTERNAL_PREFIX}voice"], stdout="\n".join(logs) + "\n")

        http = JsonHttpClient(timeout=600.0)
        provider_adapter = self._voice_provider_registry(
            http=http,
            account=account,
            identity=identity,
            logs=logs,
            start_service_if_needed=start_service_if_needed,
            progress_hook=progress_hook,
        ).get(provider)
        provider_adapter.prepare()
        generated = 0
        cancelled = False
        failures: list[str] = []
        for position, job in enumerate(pending, start=1):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                remaining = len(pending) - position + 1
                emit(f"[取消] 用户取消，跳过剩余 {remaining} 条。已完成 {generated} 条。")
                break
            try:
                emit(f"[生成 {position}/{len(pending)}] {job.product_name} / {job.block['block_label']}")
                filename = Path(self._voice_filename(job)).with_suffix(
                    provider_adapter.capabilities.output_suffix
                ).name
                # Generate beside the previous artifact first. The database update decides
                # which file is current; older files remain until a confirmed cleanup batch.
                final_path = unique_path(out_dir / filename)
                synthesis = provider_adapter.synthesize(
                    TtsSynthesisRequest(
                        text=safe_text(job.block.get("body")),
                        identity=identity,
                        output_path=final_path,
                        settings=synthesis_settings,
                    )
                )
                path = synthesis.audio_path
                try:
                    self._upsert_voice_asset(
                        project_id,
                        job=job,
                        account=account,
                        path=path,
                        identity=identity,
                    )
                except Exception:
                    try:
                        if path.is_file():
                            path.unlink()
                    except OSError:
                        pass
                    raise
                generated += 1
                emit(f"[成功] {path}")
            except Exception as exc:
                failures.append(f"{job.product_name} / {job.block['block_label']}：{exc}")
                emit(f"[失败] {failures[-1]}")
        if cancelled:
            status = "cancelled"
        elif failures:
            status = "partial"
        else:
            status = "success"
        self.db.log_event(
            project_id,
            "voice_generate",
            status,
            f"{voice_provider_label(provider)} 配音{'已取消' if cancelled else '生成完成'}：新增 {generated}，失败 {len(failures)}，跳过 {len(existing)}",
            [{"item_kind": "voice", "status": "failed", "message": item} for item in failures],
        )
        return WorkflowRunResult(
            [f"{INTERNAL_PREFIX}voice"],
            returncode=0 if not failures else 1,
            stdout="\n".join(logs) + "\n",
            stderr="\n".join(failures),
        )

    def _voice_provider_registry(
        self,
        *,
        http: "JsonHttpClient",
        account: dict[str, Any],
        identity: VoiceSynthesisIdentity,
        logs: list[str],
        start_service_if_needed: bool,
        progress_hook: Callable[[str], None] | None,
    ) -> TtsProviderRegistry:
        def prepare_indextts() -> None:
            self._ensure_tts_api_ready(
                http,
                logs=logs,
                start_if_needed=start_service_if_needed,
                progress_hook=progress_hook,
            )
            self._ensure_registered_voice(
                http,
                voice_id=identity.voice_id,
                account=account,
                logs=logs,
                progress_hook=progress_hook,
            )

        def prepare_minimax() -> None:
            self._prepare_minimax_voice(
                identity.voice_id,
                logs=logs,
                progress_hook=progress_hook,
            )

        registry = TtsProviderRegistry()
        registry.register(
            IndexTtsProvider(
                http=http,
                endpoint=f"{DEFAULT_TTS_API_BASE_URL.rstrip('/')}/v1/clone/voice",
                prepare_callback=prepare_indextts,
                finalize_callback=self._finalize_generated_voice,
            )
        )
        registry.register(
            MiniMaxTtsProvider(
                prepare_callback=prepare_minimax,
                synthesize_callback=lambda request: self._synthesize_minimax_to_path(
                    request.text,
                    voice_id=request.identity.voice_id,
                    final_path=request.output_path,
                    model=request.identity.model,
                    **request.settings,
                ),
            )
        )
        return registry

    def _voice_configuration_for_account(
        self,
        account: dict[str, Any],
        provider: str,
    ) -> tuple[VoiceSynthesisIdentity, dict[str, Any]] | None:
        normalized = normalize_voice_provider(provider)
        profile = self.repo.account_voice_profile(int(account["id"]), normalized)
        voice_id = safe_text(profile.get("voice_id")) if profile else ""
        if not voice_id:
            voice_id = account_voice_id_for_provider(account, normalized)
        if not voice_id:
            return None

        settings = default_voice_synthesis_settings(normalized)
        model = default_voice_model(normalized)
        if profile:
            model = safe_text(profile.get("model")) or model
            settings_text = safe_text(profile.get("settings_json"))
            if settings_text:
                try:
                    configured = json.loads(settings_text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"用户“{account.get('label')}”的 {normalized} settings_json 不是有效 JSON。"
                    ) from exc
                if not isinstance(configured, dict):
                    raise ValueError(
                        f"用户“{account.get('label')}”的 {normalized} settings_json 必须是 JSON 对象。"
                    )
                settings.update(configured)
        identity = voice_synthesis_identity(
            normalized,
            voice_id,
            model=model,
            settings=settings,
        )
        return identity, settings

    def synthesize_standalone_voice(
        self,
        text: str,
        *,
        account_label: str = "",
        reference_audio_path: str | Path | None = None,
        voice_provider: str = VOICE_PROVIDER_INDEXTTS,
        output_dir: str | Path | None = None,
        output_name: str = "",
        source_label: str = "",
        start_service_if_needed: bool = True,
        progress_hook: Callable[[str], None] | None = None,
    ) -> WorkflowRunResult:
        body = safe_text(text).strip()
        if not body:
            raise ValueError("请先输入要配音的文字，或选择包含文字的 MD 文档。")

        account_label = safe_text(account_label)
        reference_text = safe_text(reference_audio_path)
        provider = normalize_voice_provider(voice_provider)
        if provider == VOICE_PROVIDER_INDEXTTS and bool(account_label) == bool(reference_text):
            raise ValueError("请选择一个已配置用户音色，或上传一个参考音频文件，二者必须且只能选一个。")
        if provider == VOICE_PROVIDER_MINIMAX and not account_label:
            raise ValueError("MiniMax API 配音需要选择一个已配置用户音色，用它的 voice_id 调用云端配音。")

        logs: list[str] = []

        def emit(message: str) -> None:
            logs.append(message)
            if progress_hook:
                progress_hook(message)

        out_dir = Path(output_dir) if safe_text(output_dir) else DEFAULT_STANDALONE_VOICE_ROOT
        out_dir.mkdir(parents=True, exist_ok=True)

        if provider == VOICE_PROVIDER_MINIMAX:
            account = self._resolve_account(account_label)
            if not account:
                raise ValueError(f"未找到配音用户：{account_label}")
            voice_id = account_voice_id_for_provider(account, provider)
            if not voice_id:
                raise ValueError(f"用户“{account_label}”缺少 MiniMax 音色标识。请到用户管理里补齐 minimax_voice_id。")
            voice_id = self._prepare_minimax_voice(voice_id, logs=logs, progress_hook=progress_hook)
            voice_label = safe_text(account.get("voice_name") or account.get("label") or voice_id)
            filename = safe_text(output_name) or standalone_voice_filename(
                voice_label=voice_label,
                source_label=source_label,
                text=body,
            )
            filename_stem = Path(filename).stem if Path(filename).suffix else filename
            final_path = unique_path(out_dir / f"{safe_path_component(filename_stem)}.mp3")
            emit(f"[配音任务] MiniMax API 文本 {len(body)} 字，输出目录：{out_dir}")
            output_path = self._synthesize_minimax_to_path(body, voice_id=voice_id, final_path=final_path)
            emit(f"[成功] {output_path}")
            return WorkflowRunResult(
                [f"{INTERNAL_PREFIX}standalone-voice", "--voice-provider", VOICE_PROVIDER_MINIMAX],
                stdout="\n".join(logs) + "\n",
            )

        http = JsonHttpClient(timeout=600.0)
        self._ensure_tts_api_ready(http, logs=logs, start_if_needed=start_service_if_needed, progress_hook=progress_hook)

        if account_label:
            account = self._resolve_account(account_label)
            if not account:
                raise ValueError(f"未找到配音用户：{account_label}")
            voice_id = account_voice_id_for_provider(account, VOICE_PROVIDER_INDEXTTS)
            if not voice_id:
                raise ValueError(f"用户“{account_label}”缺少 IndexTTS 音色标识。请到用户管理里补齐。")
            self._ensure_registered_voice(http, voice_id=voice_id, account=account, logs=logs, progress_hook=progress_hook)
            voice_label = safe_text(account.get("voice_name") or account.get("label") or voice_id)
            endpoint = f"{DEFAULT_TTS_API_BASE_URL.rstrip('/')}/v1/clone/voice"
            payload = {"voice_id": voice_id, "text": body, **DEFAULT_TTS_FIELDS}
            emit(f"[音色] 使用已配置用户音色：{account_label}")
        else:
            reference = Path(reference_text)
            if not reference.exists():
                raise ValueError(f"参考音频文件不存在：{reference}")
            if not reference.is_file():
                raise ValueError(f"参考音频路径不是文件：{reference}")
            voice_label = reference.stem
            endpoint = f"{DEFAULT_TTS_API_BASE_URL.rstrip('/')}/v1/clone"
            payload = {"speaker_audio_path": str(reference), "text": body, **DEFAULT_TTS_FIELDS}
            emit(f"[音色] 使用参考音频文件：{reference}")

        filename = safe_text(output_name) or standalone_voice_filename(
            voice_label=voice_label,
            source_label=source_label,
            text=body,
        )
        filename_stem = Path(filename).stem if Path(filename).suffix else filename
        final_path = unique_path(out_dir / f"{safe_path_component(filename_stem)}.wav")
        payload["output_name"] = final_path.name
        emit(f"[配音任务] 文本 {len(body)} 字，输出目录：{out_dir}")
        api_result = http.post(endpoint, json_payload=payload)
        if not isinstance(api_result, dict):
            raise ValueError(f"配音接口返回异常：{api_result}")
        generated_path = Path(safe_text(api_result.get("audio_path")))
        if not generated_path.exists():
            raise ValueError(f"配音接口返回成功，但没有找到音频文件：{generated_path}")
        output_path = self._finalize_generated_voice(generated_path, final_path)
        emit(f"[成功] {output_path}")
        return WorkflowRunResult(
            [f"{INTERNAL_PREFIX}standalone-voice"],
            stdout="\n".join(logs) + "\n",
        )

    def assemble_spoken_script(
        self,
        project_id: int,
        *,
        mode: str = "standard",
        account_label: str = "",
        intro_index: int = 1,
        top_uids: str | list[str] | None = None,
        product_uids: str | list[str] | None = None,
        product_order_strategy: str = DEFAULT_PRODUCT_ORDER_STRATEGY,
        output_markdown_path: str | Path | None = None,
        display_template: str = "",
    ) -> WorkflowRunResult:
        project = self._required_project(project_id)
        account = self._resolve_account(account_label)
        output_markdown = self._spoken_markdown_path(project, output_markdown_path)
        manifest_path = self.spoken_manifest_path(project_id, output_markdown)
        order_strategy = self._validate_product_order_strategy(product_order_strategy)
        top_uid_list = split_csv(top_uids) if isinstance(top_uids, str) else list(top_uids or [])
        product_uid_list = split_csv(product_uids) if isinstance(product_uids, str) else list(product_uids or [])
        products = self._ordered_products(
            project_id,
            mode=mode,
            top_uids=top_uid_list,
            product_uids=product_uid_list,
            product_order_strategy=order_strategy,
        )
        blocks = self.repo.script_blocks(project_id)
        assets = self.repo.asset_bindings(project_id)
        product_blocks: dict[str, list[dict[str, Any]]] = {}
        intro_blocks: list[dict[str, Any]] = []
        price_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if block["script_type"] == "product":
                product_blocks.setdefault(block["owner_uid"], []).append(block)
            elif block["script_type"] == "intro":
                intro_blocks.append(block)
            elif block["script_type"] == "price_transition":
                price_blocks.append(block)

        if not intro_blocks and not product_blocks and not price_blocks:
            md_path = safe_text(project.get("md_path"))
            raise ValueError(
                "当前项目还没有同步到任何口播文案块，不能生成口播稿。\n"
                f"请先在“同步中心”同步商品文案 MD，或检查项目绑定的商品文案路径：{md_path or '未配置'}"
            )

        lines: list[str] = []
        entries: list[dict[str, Any]] = []
        order = 1
        account_label = safe_text(account.get("label") or account_label)
        account_id = safe_text(account.get("account_id"))
        voice_scope = self._voice_scope_fragment(project, account_label)

        if intro_blocks:
            intro_block = intro_blocks[min(max(1, intro_index), len(intro_blocks)) - 1]
            self._append_spoken_paragraph(lines, intro_block["body"])
            self._raise_if_missing_voice(
                project_id,
                account_label=account_label,
                block=intro_block,
                assets=assets,
                uid="INTRO",
                block_label=safe_text(intro_block.get("block_label")),
                product={},
            )
            entries.append(
                self._manifest_entry(
                    order=order,
                    entry_type="transition",
                    section="intro",
                    block=intro_block,
                    account_label=account_label,
                    account_id=account_id,
                    assets=assets,
                    product={},
                    source_label=intro_block["block_label"],
                    display_template=display_template,
                    preferred_voice_path_contains=voice_scope,
                )
            )
            order += 1

        top_set = {item.casefold() for item in top_uid_list}
        used_price_labels: set[str] = set()
        for product in products:
            is_top_product = product["uid"].casefold() in top_set
            if not is_top_product:
                price_block = self._matching_price_block(product, price_blocks)
                if price_block:
                    price_key = safe_text(price_block.get("price_range_label")) or str(price_block["id"])
                    if price_key not in used_price_labels:
                        used_price_labels.add(price_key)
                        self._append_spoken_paragraph(lines, price_block["body"])
                        self._raise_if_missing_voice(
                            project_id,
                            account_label=account_label,
                            block=price_block,
                            assets=assets,
                            uid="PRICE_TRANSITION",
                            block_label=safe_text(price_block.get("price_range_label")),
                            product={},
                        )
                        entries.append(
                            self._manifest_entry(
                                order=order,
                                entry_type="transition",
                                section="price_transition",
                                block=price_block,
                                account_label=account_label,
                                account_id=account_id,
                                assets=assets,
                                product={},
                                source_label=f"价格过渡 {price_block['price_range_label']}",
                                display_template=display_template,
                                preferred_voice_path_contains=voice_scope,
                            )
                        )
                        order += 1
            versions = product_blocks.get(product["uid"], [])
            if not versions:
                continue
            block = self._choose_voice_ready_block(versions, assets, uid=product["uid"], account_label=account_label) or versions[0]
            self._raise_if_missing_voice(
                project_id,
                account_label=account_label,
                block=block,
                assets=assets,
                uid=product["uid"],
                product=product,
            )
            self._append_spoken_paragraph(lines, block["body"])
            entries.append(
                self._manifest_entry(
                    order=order,
                    entry_type="product",
                    section="top" if is_top_product else "product",
                    block=block,
                    account_label=account_label,
                    account_id=account_id,
                    assets=assets,
                    product=product,
                    source_label=block["block_label"],
                    display_template=display_template,
                    preferred_voice_path_contains=voice_scope,
                )
            )
            order += 1

        if order == 1:
            md_path = safe_text(project.get("md_path"))
            selected_text = "、".join(product_uid_list or top_uid_list)
            raise ValueError(
                "没有找到可写入正文的引言、商品文案或价格过渡文案，已停止生成，避免只输出结尾文案。\n"
                f"当前筛选：{selected_text or '全部商品'}\n"
                f"请先同步商品文案 MD，或检查 Top UID 是否存在于文案中。项目文案路径：{md_path or '未配置'}"
            )

        self._append_spoken_paragraph(lines, DEFAULT_CLOSING_TEXT)
        entries.append(
            self._closing_manifest_entry(
                order=order,
                text=DEFAULT_CLOSING_TEXT,
                account=account,
                account_label=account_label,
                account_id=account_id,
            )
        )

        output_markdown.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        manifest = {
            "version": 2,
            "source": "bworkflow-sql",
            "project_id": project_id,
            "project_name": safe_text(project.get("name")),
            "category": safe_text(project.get("category_name")),
            "mode": mode,
            "product_order_strategy": order_strategy,
            "account_label": account_label,
            "account_id": account_id,
            "display_template": display_template,
            "spoken_markdown_path": str(output_markdown),
            "closing_text": DEFAULT_CLOSING_TEXT,
            "created_at": now_iso(),
            "entries": entries,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.log_event(project_id, "spoken_assembly", "success", f"口播稿已生成：{output_markdown}；manifest：{manifest_path}")
        return WorkflowRunResult(
            [f"{INTERNAL_PREFIX}assembly"],
            stdout=f"口播稿已生成：{output_markdown}\nManifest 已写入内部目录：{manifest_path}\n条目：{len(entries)}\n",
        )

    def assemble_spoken_script_plan(
        self,
        project_id: int,
        *,
        mode: str = "standard",
        account_label: str = "",
        intro_index: int = 1,
        top_uids: str | list[str] | None = None,
        product_uids: str | list[str] | None = None,
        product_order_strategy: str = DEFAULT_PRODUCT_ORDER_STRATEGY,
    ) -> dict[str, Any]:
        project = self._required_project(project_id)
        account = self._resolve_account(account_label)
        account_label = safe_text(account.get("label") or account_label)
        order_strategy = self._validate_product_order_strategy(product_order_strategy)
        top_uid_list = split_csv(top_uids) if isinstance(top_uids, str) else list(top_uids or [])
        product_uid_list = split_csv(product_uids) if isinstance(product_uids, str) else list(product_uids or [])
        products = self._ordered_products(
            project_id,
            mode=mode,
            top_uids=top_uid_list,
            product_uids=product_uid_list,
            product_order_strategy=order_strategy,
        )
        blocks = self.repo.script_blocks(project_id)
        assets = self.repo.asset_bindings(project_id)
        product_blocks: dict[str, list[dict[str, Any]]] = {}
        intro_blocks: list[dict[str, Any]] = []
        price_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if block["script_type"] == "product":
                product_blocks.setdefault(block["owner_uid"], []).append(block)
            elif block["script_type"] == "intro":
                intro_blocks.append(block)
            elif block["script_type"] == "price_transition":
                price_blocks.append(block)

        sequence: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        order = 1
        if intro_blocks:
            intro_block = intro_blocks[min(max(1, intro_index), len(intro_blocks)) - 1]
            sequence.append(_assembly_plan_entry(order, "intro", intro_block))
            if not self._voice_ready_for_block(
                assets,
                uid="INTRO",
                block=intro_block,
                account_label=account_label,
                block_label=safe_text(intro_block.get("block_label")),
            ):
                issues.append(self._missing_voice_issue(project_id, account_label=account_label, block=intro_block, uid="INTRO", product={}))
            order += 1
        else:
            issues.append({"level": "warning", "code": "missing_intro_block", "message": "no synced intro block"})

        top_set = {item.casefold() for item in top_uid_list}
        used_price_labels: set[str] = set()
        for product in products:
            uid = safe_text(product.get("uid"))
            is_top_product = uid.casefold() in top_set
            if not is_top_product:
                price_block = self._matching_price_block(product, price_blocks)
                if price_block:
                    price_key = safe_text(price_block.get("price_range_label")) or str(price_block["id"])
                    if price_key not in used_price_labels:
                        used_price_labels.add(price_key)
                        sequence.append(_assembly_plan_entry(order, "price_transition", price_block))
                        if not self._voice_ready_for_block(
                            assets,
                            uid="PRICE_TRANSITION",
                            block=price_block,
                            account_label=account_label,
                            block_label=safe_text(price_block.get("price_range_label")),
                        ):
                            issues.append(
                                self._missing_voice_issue(
                                    project_id,
                                    account_label=account_label,
                                    block=price_block,
                                    uid="PRICE_TRANSITION",
                                    product={},
                                )
                            )
                        order += 1
            versions = product_blocks.get(uid, [])
            if not versions:
                issues.append(
                    {
                        "level": "warning",
                        "code": "missing_product_block",
                        "uid": uid,
                        "title": safe_text(product.get("title")),
                    }
                )
                continue
            block = self._choose_voice_ready_block(versions, assets, uid=uid, account_label=account_label) or versions[0]
            sequence.append(_assembly_plan_entry(order, "product", block, product=product))
            if not self._voice_ready_for_block(assets, uid=uid, block=block, account_label=account_label):
                issues.append(self._missing_voice_issue(project_id, account_label=account_label, block=block, uid=uid, product=product))
            order += 1

        sequence.append(
            {
                "order": order,
                "section": "closing",
                "script_type": "closing",
                "script_id": "closing",
                "text_hash": text_hash(DEFAULT_CLOSING_TEXT),
            }
        )
        content_incomplete = any(issue["code"] == "missing_product_block" for issue in issues)
        voice_incomplete = any(issue["code"] == "missing_voice_asset" for issue in issues)
        if content_incomplete:
            status = "content_incomplete"
        elif voice_incomplete:
            status = "voice_incomplete"
        else:
            status = "ready_to_assemble"
        assemble_command = self._assemble_cli_command(
            project_id,
            account_label=account_label,
            intro_index=intro_index,
            mode=mode,
            top_uids=top_uid_list,
            product_uids=product_uid_list,
            product_order_strategy=order_strategy,
        )
        return {
            "ok": status == "ready_to_assemble",
            "status": status,
            "project": {
                "id": int(project_id),
                "name": safe_text(project.get("name")),
                "scheme_id": safe_text(project.get("scheme_id")),
                "scheme_name": safe_text(project.get("scheme_name")),
            },
            "summary": {
                "products_total": len(products),
                "intro_blocks": len(intro_blocks),
                "product_blocks": sum(len(value) for value in product_blocks.values()),
                "price_transition_blocks": len(price_blocks),
                "sequence_entries": len(sequence),
            },
            "sequence": sequence,
            "issues": issues,
            "next": {
                "action": "assemble" if status == "ready_to_assemble" else ("generate_voice" if status == "voice_incomplete" else "sync_or_fill_content"),
                "command": assemble_command if status == "ready_to_assemble" else (
                    f"python -m bworkflow_sql voice-counts {project_id} --account {account_label}"
                    if status == "voice_incomplete"
                    else f"python -m bworkflow_sql script-doctor {project_id}"
                ),
                "follow_up_command": f"python -m bworkflow_sql voice {project_id} --account {account_label}" if status == "voice_incomplete" else "",
            },
        }

    def generate_jianying_draft(
        self,
        project_id: int,
        *,
        manifest_path: str | Path,
        draft_name: str,
        draft_root: str | Path,
        intro_video_path: str | Path | None = None,
        include_subtitles: bool = False,
        subtitle_no_vad: bool = False,
    ) -> WorkflowRunResult:
        _project = self._required_project(project_id)
        manifest = Path(manifest_path)
        if not manifest.exists():
            raise ValueError(f"缺少内部 manifest，请先组合口播稿：{manifest}")
        intro_video = Path(safe_text(intro_video_path)) if safe_text(intro_video_path) else None
        if intro_video is not None and not intro_video.exists():
            raise ValueError(f"引言成片视频不存在：{intro_video}")
        effective_manifest = self._jianying_manifest_for_intro_video(project_id, manifest, intro_video=intro_video)
        script = self._jianying_engine_script()
        python_exe = self._jianying_engine_python(script)
        if not script.exists():
            raise ValueError(f"剪映草稿生成引擎不存在：{script}")
        cmd = [
            str(python_exe),
            str(script),
            "--manifest",
            str(effective_manifest),
            "--draft-name",
            safe_path_component(draft_name or "B-Workflow-SQL"),
            "--draft-root",
            str(draft_root),
            "--allow-replace",
        ]
        if include_subtitles:
            if subtitle_no_vad:
                cmd.append("--subtitle-no-vad")
        else:
            cmd.append("--skip-subtitles")
        if intro_video is not None:
            cmd += ["--intro-video", str(intro_video)]
        completed = run_subprocess_text(cmd)
        completed.stdout = format_jianying_run_stdout(completed.stdout)
        self.db.log_event(
            project_id,
            "jianying_draft",
            "success" if completed.returncode == 0 else "failed",
            f"剪映草稿生成完成，退出码 {completed.returncode}",
        )
        return completed

    def _jianying_engine_script(self) -> Path:
        candidates = [
            JIANYING_ENGINE_DIR / "generate_jianying_draft.py",
            LEGACY_B_WORKFLOW_SKILL_SCRIPTS / "generate_jianying_draft.py",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _jianying_engine_python(self, script: Path) -> Path:
        override = safe_text(os.environ.get("BWORKFLOW_JIANYING_PYTHON"))
        if override:
            return Path(override)
        candidates = [
            script.parent / ".venv" / "Scripts" / "python.exe",
            LEGACY_B_WORKFLOW_SKILL_SCRIPTS.parent / ".venv" / "Scripts" / "python.exe",
            Path(sys.executable),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return Path(sys.executable)

    def _run_internal(self, cmd: list[str]) -> WorkflowRunResult:
        args = self._parse_internal_args(cmd[1:])
        project_id = int(args.get("project-id") or "0")
        if cmd[0] == f"{INTERNAL_PREFIX}voice":
            return self.generate_voice(
                project_id,
                account_label=args.get("account-label", ""),
                voice_provider=args.get("voice-provider", VOICE_PROVIDER_INDEXTTS),
                uids=split_csv(args.get("uids", "")) or None,
                script_ids=split_csv(args.get("script-ids", "")) or None,
                output_dir=args.get("output-dir", ""),
            )
        if cmd[0] == f"{INTERNAL_PREFIX}assembly":
            return self.assemble_spoken_script(
                project_id,
                mode=args.get("mode", "standard"),
                account_label=args.get("account-label", ""),
                intro_index=int(args.get("intro-index") or "1"),
                top_uids=split_csv(args.get("top-uids", "")),
                product_order_strategy=args.get("product-order-strategy", DEFAULT_PRODUCT_ORDER_STRATEGY),
                product_uids=split_csv(args.get("uids", "")),
                output_markdown_path=args.get("output-markdown", ""),
                display_template=args.get("display-template", ""),
            )
        if cmd[0] == f"{INTERNAL_PREFIX}jianying":
            return self.generate_jianying_draft(
                project_id,
                manifest_path=args.get("manifest", ""),
                draft_name=args.get("draft-name", ""),
                draft_root=args.get("draft-root", DEFAULT_JIANYING_DRAFT_ROOT),
                intro_video_path=args.get("intro-video", ""),
            )
        raise ValueError(f"未知内部任务：{cmd[0]}")

    def _parse_internal_args(self, parts: list[str]) -> dict[str, str]:
        args: dict[str, str] = {}
        index = 0
        while index < len(parts):
            key = parts[index]
            if key.startswith("--") and index + 1 < len(parts):
                args[key[2:]] = parts[index + 1]
                index += 2
            else:
                index += 1
        return args

    def _jianying_manifest_for_intro_video(self, project_id: int, manifest: Path, *, intro_video: Path | None) -> Path:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            return manifest
        changed = self._refresh_manifest_display_video_slots(payload)
        entries = [
            entry
            for entry in payload["entries"]
            if not (isinstance(entry, dict) and safe_text(entry.get("section")) == "intro")
        ] if intro_video is not None else payload["entries"]
        if intro_video is not None:
            if entries != payload["entries"]:
                changed = True
            payload["entries"] = entries
            payload["intro_video_path"] = str(intro_video)
            changed = True
        if not changed:
            return manifest
        output_dir = self._internal_project_dir(project_id) / "jianying"
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = "with-intro-video" if intro_video is not None else "jianying-ready"
        output = output_dir / f"{manifest.stem}.{suffix}.json"
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output

    def _refresh_manifest_display_video_slots(self, payload: dict[str, Any]) -> bool:
        display_template = safe_text(payload.get("display_template"))
        if not display_template:
            return False
        try:
            from .template_config import get_template_slot

            slot = get_template_slot(display_template)
        except ValueError:
            return False
        changed = False
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            if not safe_text(entry.get("display_video_path") or entry.get("video_path")):
                continue
            if entry.get("display_video_slot") != slot:
                entry["display_video_slot"] = dict(slot)
                changed = True
        return changed

    def _required_project(self, project_id: int) -> dict[str, Any]:
        project = self.repo.project(project_id)
        if not project:
            raise ProjectNotFoundError("请先选择品类项目。")
        return project

    def _intro_block_for_label(self, project_id: int, intro_label: str) -> dict[str, Any]:
        intro_blocks = [
            block
            for block in self.repo.script_blocks(project_id)
            if safe_text(block.get("script_type")) == "intro"
        ]
        if not intro_blocks:
            raise ValueError("current project has no synced intro block")
        label = safe_text(intro_label)
        if label:
            for block in intro_blocks:
                if safe_text(block.get("block_label")) == label:
                    return block
        return intro_blocks[0]

    def _intro_video_output_path(
        self,
        project_id: int,
        *,
        account_label: str,
        intro_label: str,
        output_path: str | Path | None,
    ) -> Path:
        if output_path:
            target = Path(output_path).expanduser()
            target = target.resolve() if target.is_absolute() else (Path.cwd() / target).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            return target
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        stem = (
            f"intro-video-{safe_path_component(intro_label or 'intro')}-"
            f"{safe_path_component(account_label)}-subtitle-{timestamp}.mp4"
        )
        target = default_intro_plan_workspace(project_id) / stem
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.resolve()

    def _resolve_account(self, label: str) -> dict[str, Any]:
        accounts = self.repo.accounts()
        if label:
            for account in accounts:
                if account["label"] == label:
                    return account
        return accounts[0] if accounts else {}

    def _voice_profile(self, voice_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM voice_profiles WHERE voice_id=?", (voice_id,))
        return dict(row) if row else {}

    def _internal_project_dir(self, project_id: int) -> Path:
        target = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def spoken_manifest_path(self, project_id: int, markdown_path: str | Path) -> Path:
        manifest_dir = self._internal_project_dir(project_id) / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(markdown_path).stem or "口播稿"
        return manifest_dir / f"{safe_path_component(stem)}.manifest.json"

    def default_subtitle_srt_path(self, project_id: int, markdown_path: str | Path) -> Path:
        subtitle_dir = self._internal_project_dir(project_id) / "subtitles"
        subtitle_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(markdown_path).stem or "口播稿"
        if stem.endswith(".manifest"):
            stem = Path(stem).stem or "口播稿"
        return subtitle_dir / f"字幕-{safe_path_component(stem)}.srt"

    def export_subtitle_srt(
        self,
        project_id: int,
        *,
        manifest_path: str | Path,
        output_path: str | Path | None = None,
        intro_video_path: str | Path | None = None,
        intro_video_text: str = "",
        align_with_asr: bool = False,
        subtitle_asr_model: str = DEFAULT_SUBTITLE_ASR_MODEL,
        subtitle_asr_language: str = DEFAULT_SUBTITLE_ASR_LANGUAGE,
        subtitle_asr_workers: int = DEFAULT_SUBTITLE_ASR_WORKERS,
        subtitle_asr_provider: str | None = None,
    ) -> WorkflowRunResult:
        self._required_project(project_id)
        manifest = Path(manifest_path)
        if not manifest.exists():
            raise ValueError(f"缺少内部 manifest，请先组合口播稿：{manifest}")

        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
        entries = subtitle_manifest_entries(payload)
        intro_video = Path(safe_text(intro_video_path)) if safe_text(intro_video_path) else None
        initial_offset = 0.0
        if intro_video is not None:
            if not intro_video.exists():
                raise ValueError(f"引言成片视频不存在：{intro_video}")
            initial_offset = probe_media_duration_seconds(intro_video)
            entries = [entry for entry in entries if safe_text(entry.get("section")) != "intro"]

        target = Path(output_path) if output_path else self.default_subtitle_srt_path(project_id, manifest.stem)
        target.parent.mkdir(parents=True, exist_ok=True)

        missing_text: list[str] = []
        missing_audio: list[str] = []
        srt_items: list[tuple[float, float, str]] = []
        asr_jobs: list[dict[str, Any]] = []
        intro_text = safe_text(intro_video_text).strip() if intro_video is not None else ""
        if intro_text:
            if align_with_asr and intro_video is not None:
                asr_jobs.append({"label": "片头视频", "audio_path": str(intro_video), "text": intro_text, "offset_sec": 0.0})
            else:
                srt_items.extend(distribute_subtitle_text(intro_text, 0.0, initial_offset))
        cursor = initial_offset
        for entry in entries:
            label = subtitle_entry_label(entry)
            text = safe_text(entry.get("text")).strip()
            audio_text = safe_text(entry.get("audio_path"))
            if not text:
                missing_text.append(label)
            if not audio_text:
                missing_audio.append(label)
            if not text or not audio_text:
                continue
            audio_path = Path(audio_text)
            if not audio_path.is_absolute():
                audio_path = manifest.parent / audio_path
            if not audio_path.exists():
                missing_audio.append(f"{label}：{audio_path}")
                continue
            duration = probe_media_duration_seconds(audio_path)
            if align_with_asr:
                asr_jobs.append({"label": label, "audio_path": str(audio_path), "text": text, "offset_sec": cursor})
            else:
                for start, end, chunk_text in distribute_subtitle_text(text, cursor, duration):
                    srt_items.append((start, end, chunk_text))
            cursor += duration

        if missing_text or missing_audio:
            detail: list[str] = []
            if missing_text:
                detail.append("缺字幕文本：" + "；".join(missing_text[:8]))
            if missing_audio:
                detail.append("缺配音文件：" + "；".join(missing_audio[:8]))
            raise ValueError("\n".join(detail))
        if asr_jobs:
            srt_items.extend(
                align_subtitle_jobs_with_asr(
                    asr_jobs,
                    model_name=subtitle_asr_model,
                    language=subtitle_asr_language,
                    beam_size=DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
                    workers=subtitle_asr_workers,
                    provider_name=subtitle_asr_provider,
                )
            )
        if not srt_items:
            raise ValueError("manifest 中没有可导出的字幕条目。")

        target.write_text(format_srt(srt_items), encoding="utf-8-sig")
        total_duration = srt_items[-1][1]
        stdout = (
            f"字幕 SRT 已导出：{target}\n"
            f"字幕条数：{len(srt_items)}\n"
            f"总时长：{total_duration:.3f} 秒\n"
        )
        if align_with_asr:
            stdout += (
                f"精确原文强制对齐: {subtitle_asr_model} "
                f"(batch={max(1, int(subtitle_asr_workers or 1))})\n"
            )
        if intro_video is not None:
            stdout += f"引言成片偏移：{initial_offset:.3f} 秒\n"
        return WorkflowRunResult([f"{INTERNAL_PREFIX}subtitle"], stdout=stdout)

    def preview_roll_b_rename(self, project_id: int, directory: str | Path) -> dict[str, Any]:
        self._required_project(project_id)
        root = Path(directory)
        products = {safe_text(item.get("uid")).casefold(): item for item in self.repo.products(project_id, include_removed=False)}
        items: list[RollBRenameItem] = []
        blockers: list[str] = []
        if not safe_text(directory):
            blockers.append("还没有选择视频目录。")
        elif not root.exists():
            blockers.append(f"视频目录不存在：{root}")
        elif not root.is_dir():
            blockers.append(f"选择的路径不是目录：{root}")
        if not products:
            blockers.append("当前项目还没有同步 Master 商品。请先到“同步中心”同步 Master。")
        if blockers:
            return self._roll_b_result(root, items, blockers)

        files = sorted(
            [path for path in root.iterdir() if path.is_file() and path.suffix.casefold() in ROLL_B_VIDEO_SUFFIXES],
            key=lambda path: path.name.casefold(),
        )
        if not files:
            blockers.append("目录下没有可处理的视频文件（mp4 / mov / mkv / avi）。")
            return self._roll_b_result(root, items, blockers)

        product_uids = sorted((safe_text(item.get("uid")) for item in products.values()), key=lambda value: len(value), reverse=True)
        grouped: dict[str, list[Path]] = {}
        unmatched: list[RollBRenameItem] = []
        for path in files:
            matched = [uid for uid in product_uids if uid and uid.casefold() in path.stem.casefold()]
            if len(matched) == 1:
                grouped.setdefault(matched[0].casefold(), []).append(path)
            elif len(matched) > 1:
                unmatched.append(self._roll_b_item(path, "", "", "", "blocked", f"文件名匹配到多个 UID：{'、'.join(matched[:5])}"))
            else:
                unmatched.append(self._roll_b_item(path, "", "", "", "skipped", "文件名没有匹配到当前项目的商品 UID。"))

        planned: list[RollBRenameItem] = []
        for uid_key, paths in grouped.items():
            paths = sorted(paths, key=lambda path: (path.stem.casefold() != uid_key, path.name.casefold()))
            product = products[uid_key]
            uid = safe_text(product.get("uid"))
            title = safe_text(product.get("title"))
            price = self._roll_b_price_label(safe_text(product.get("price_label")))
            if not title or not price:
                for path in paths:
                    planned.append(self._roll_b_item(path, uid, title, price, "blocked", "Master 商品缺少价格或商品名称。"))
                continue
            for index, path in enumerate(paths, start=1):
                suffix = f"-{index}" if len(paths) > 1 else ""
                target_name = safe_path_component(f"{price}-{uid}-{title}{suffix}") + path.suffix
                target = path.with_name(target_name)
                status = "unchanged" if path.name == target_name else "rename"
                message = "已是目标格式，无需改名。" if status == "unchanged" else "将改名。"
                planned.append(
                    RollBRenameItem(
                        source_path=str(path),
                        target_path=str(target),
                        source_name=path.name,
                        target_name=target_name,
                        uid=uid,
                        title=title,
                        price_label=price,
                        status=status,
                        message=message,
                    )
                )

        source_paths = {Path(item.source_path).resolve() for item in planned}
        moving_sources = {Path(item.source_path).resolve() for item in planned if item.status == "rename"}
        for item in planned:
            if item.status != "rename":
                continue
            target = Path(item.target_path)
            target_resolved = target.resolve() if target.exists() else target.absolute()
            if target.exists() and target_resolved not in source_paths:
                item.status = "blocked"
                item.message = f"目标文件已存在：{target.name}"
            elif target.exists() and target_resolved in source_paths and target_resolved not in moving_sources:
                item.status = "blocked"
                item.message = f"目标文件被本目录中的另一个文件占用：{target.name}"

        items = planned + unmatched
        return self._roll_b_result(root, items, blockers)

    def execute_roll_b_rename(self, project_id: int, directory: str | Path) -> dict[str, Any]:
        preview = self.preview_roll_b_rename(project_id, directory)
        items = [dict(item) for item in preview.get("items", [])]
        actionable = [item for item in items if item.get("status") == "rename"]
        if not actionable:
            preview["renamed"] = 0
            preview["result_message"] = "没有需要改名的视频文件。"
            return preview
        if any(item.get("status") == "blocked" for item in items):
            raise ValueError("预览中存在阻塞项，请修正后再执行。")

        temp_pairs: list[tuple[Path, Path, Path]] = []
        renamed_items: list[dict[str, str]] = []
        try:
            for index, item in enumerate(actionable, start=1):
                source = Path(item["source_path"])
                target = Path(item["target_path"])
                if not source.exists():
                    raise ValueError(f"源文件不存在：{source}")
                temp = source.with_name(f".{source.name}.rollbtmp-{int(time.time() * 1000)}-{index}{source.suffix}")
                while temp.exists():
                    temp = source.with_name(f".{source.name}.rollbtmp-{int(time.time() * 1000)}-{index + 1}{source.suffix}")
                source.rename(temp)
                temp_pairs.append((temp, target, source))
            for temp, target, source in temp_pairs:
                target.parent.mkdir(parents=True, exist_ok=True)
                temp.rename(target)
                renamed_items.append({"source_path": str(source), "target_path": str(target), "target_name": target.name})
        except Exception:
            for temp, _target, source in reversed(temp_pairs):
                if temp.exists() and not source.exists():
                    try:
                        temp.rename(source)
                    except OSError:
                        pass
            raise

        refreshed = self.preview_roll_b_rename(project_id, directory)
        refreshed["renamed"] = len(renamed_items)
        refreshed["renamed_items"] = renamed_items
        refreshed["result_message"] = f"已改名 {len(renamed_items)} 个视频文件。"
        return refreshed

    def _roll_b_item(self, path: Path, uid: str, title: str, price: str, status: str, message: str) -> RollBRenameItem:
        return RollBRenameItem(
            source_path=str(path),
            target_path="",
            source_name=path.name,
            target_name="",
            uid=uid,
            title=title,
            price_label=price,
            status=status,
            message=message,
        )

    def _roll_b_result(self, root: Path, items: list[RollBRenameItem], blockers: list[str]) -> dict[str, Any]:
        counts = {
            "rename": sum(1 for item in items if item.status == "rename"),
            "unchanged": sum(1 for item in items if item.status == "unchanged"),
            "skipped": sum(1 for item in items if item.status == "skipped"),
            "blocked": sum(1 for item in items if item.status == "blocked"),
        }
        return {
            "directory": str(root),
            "counts": counts,
            "items": [item.as_dict() for item in items],
            "blockers": blockers,
            "can_execute": counts["rename"] > 0 and counts["blocked"] == 0 and not blockers,
        }

    def _roll_b_price_label(self, value: str) -> str:
        number = first_number(value)
        if number is None:
            return safe_text(value)
        label = str(int(number)) if number.is_integer() else str(number)
        return f"{label}元"

    def _voice_output_dir(self, project: dict[str, Any], *, account: dict[str, Any], account_label: str = "") -> Path:
        label = safe_text(account.get("label") or account_label or "voice")
        root = safe_text(project.get("voice_root")) or DEFAULT_OUTPUT_ROOT
        return voice_user_dir(root, project, "" if label == "voice" else label)

    def expected_voice_output_dir(self, project_id: int, *, account_label: str = "") -> Path:
        project = self._required_project(project_id)
        account = self._resolve_account(account_label)
        return self._voice_output_dir(project, account=account, account_label=account_label)

    def _spoken_markdown_path(self, project: dict[str, Any], explicit_path: str | Path | None = None) -> Path:
        path_text = safe_text(explicit_path) or safe_text(project.get("spoken_md_path"))
        if not path_text:
            raise ValueError("请先在“组合口播稿”里选择口播稿输出 MD。")
        path = Path(path_text)
        if path.suffix.casefold() != ".md":
            raise ValueError("口播稿输出文件必须是 .md 文档。")
        path = ensure_markdown_write_target(project, path, artifact_kind="spoken")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _voice_jobs(
        self,
        project_id: int,
        *,
        uids: list[str] | None = None,
        script_ids: list[str] | None = None,
    ) -> list[VoiceJob]:
        products = {item["uid"]: item for item in self.repo.products(project_id, include_removed=False)}
        selected = {uid.casefold() for uid in (uids or [])}
        selected_scripts = {script_id.casefold() for script_id in (script_ids or [])}
        jobs: list[VoiceJob] = []
        product_index = 0
        for block in self.repo.script_blocks(project_id):
            block_script_id = safe_text(block.get("script_id")).casefold()
            if block["script_type"] == "product":
                uid = block["owner_uid"]
                product_selected = not selected and not selected_scripts
                product_selected = product_selected or (selected and uid.casefold() in selected)
                product_selected = product_selected or (selected_scripts and block_script_id in selected_scripts)
                if not product_selected:
                    continue
                product = products.get(uid)
                if not product:
                    continue
                product_index += 1
                jobs.append(
                    VoiceJob(
                        block=block,
                        uid=uid,
                        product_name=safe_text(product.get("title")),
                        price_label=safe_text(product.get("price_label")),
                        index=product_index,
                        kind="product",
                    )
                )
            elif (not selected and not selected_scripts) or (selected_scripts and block_script_id in selected_scripts):
                uid = "INTRO" if block["script_type"] == "intro" else "PRICE_TRANSITION"
                label = block["block_label"] if block["script_type"] == "intro" else block["price_range_label"]
                jobs.append(
                    VoiceJob(
                        block=block,
                        uid=uid,
                        product_name=safe_text(label),
                        index=len(jobs) + 1,
                        kind=block["script_type"],
                        price_range_label=safe_text(block.get("price_range_label")),
                    )
                )
        return jobs

    def _split_existing_voice_jobs(
        self,
        project_id: int,
        jobs: list[VoiceJob],
        account: dict[str, Any],
        *,
        identity: VoiceSynthesisIdentity,
    ) -> tuple[list[VoiceJob], list[VoiceJob]]:
        assets = self.repo.asset_bindings(project_id)
        account_label = safe_text(account.get("label"))
        existing: list[VoiceJob] = []
        pending: list[VoiceJob] = []
        for job in jobs:
            expected_fingerprint = identity.fingerprint(
                account_label=account_label,
                text_hash=safe_text(job.block.get("text_hash")),
            )
            found = False
            for asset in assets:
                if asset["asset_type"] != "voice" or asset["status"] != "ready":
                    continue
                if int(asset["script_block_id"] or 0) != int(job.block["id"]):
                    continue
                if safe_text(asset.get("account_label")) != account_label:
                    continue
                if safe_text(asset.get("text_hash")) != safe_text(job.block.get("text_hash")):
                    continue
                if safe_text(asset.get("generation_fingerprint")) != expected_fingerprint:
                    continue
                path = Path(safe_text(asset.get("path")))
                if path.exists():
                    found = True
                    break
            (existing if found else pending).append(job)
        return existing, pending

    def _generate_one_voice(
        self,
        http: "JsonHttpClient",
        *,
        job: VoiceJob,
        account: dict[str, Any],
        voice_id: str,
        output_dir: Path,
        overwrite_expired: bool = False,
    ) -> Path:
        filename = self._voice_filename(job)
        final_path = output_dir / filename if overwrite_expired else unique_path(output_dir / filename)
        payload = {
            "voice_id": voice_id,
            "text": safe_text(job.block.get("body")),
            "output_name": final_path.name,
            **DEFAULT_TTS_FIELDS,
        }
        api_result = http.post(f"{DEFAULT_TTS_API_BASE_URL.rstrip('/')}/v1/clone/voice", json_payload=payload)
        if not isinstance(api_result, dict):
            raise ValueError(f"配音接口返回异常：{api_result}")
        generated_path = Path(safe_text(api_result.get("audio_path")))
        if not generated_path.exists():
            raise ValueError(f"配音接口返回成功，但没有找到音频文件：{generated_path}")
        return self._finalize_generated_voice(generated_path, final_path)

    def _prepare_minimax_voice(
        self,
        voice_id: str,
        *,
        logs: list[str],
        progress_hook: Callable[[str], None] | None = None,
    ) -> str:
        resolved = resolve_minimax_voice_id(voice_id)
        if not resolved:
            raise ValueError("MiniMax 配音需要配置 voice_id。")
        def emit(message: str) -> None:
            logs.append(message)
            if progress_hook:
                progress_hook(message)

        api_key = load_minimax_api_key()
        emit(f"[MiniMax] 使用云端 API，无需启动本地 IndexTTS 服务。")
        if resolved in MINIMAX_KNOWN_LOCAL_VOICE_IDS:
            emit(f"[MiniMax] 使用本地已知音色：{resolved}")
            return resolved
        try:
            payload = JsonHttpClient(timeout=30.0).post(
                MINIMAX_VOICE_LIST_URL,
                json_payload={"voice_type": "all"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except Exception as exc:
            raise ValueError(f"MiniMax 音色校验失败：{exc}") from exc
        available: list[str] = []
        if isinstance(payload, dict):
            for key in ("system_voice", "voice_cloning", "voice_generation"):
                for item in payload.get(key) or []:
                    value = safe_text(item.get("voice_id") if isinstance(item, dict) else "")
                    if value:
                        available.append(value)
        if resolved not in available:
            preview = "、".join(available[:8])
            raise ValueError(f"MiniMax voice_id 不存在：{resolved}。可用音色示例：{preview}")
        emit(f"[MiniMax] 已确认音色：{resolved}")
        return resolved

    def _generate_one_minimax_voice(
        self,
        *,
        job: VoiceJob,
        voice_id: str,
        output_dir: Path,
        overwrite_expired: bool = False,
    ) -> Path:
        filename = Path(self._voice_filename(job)).with_suffix(".mp3").name
        final_path = output_dir / filename if overwrite_expired else unique_path(output_dir / filename)
        return self._synthesize_minimax_to_path(
            safe_text(job.block.get("body")),
            voice_id=voice_id,
            final_path=final_path,
        )

    def _synthesize_minimax_to_path(
        self,
        text: str,
        *,
        voice_id: str,
        final_path: Path,
        model: str = MINIMAX_T2A_MODEL,
        speed: float = 1.2,
        emotion: str = "",
        text_normalization: bool = True,
        volume: float = 1.0,
        pitch: int = 0,
        sample_rate: int = 32000,
        bitrate: int = 128000,
        format: str = "mp3",
        channel: int = 1,
    ) -> Path:
        body = safe_text(text).strip()
        if not body:
            raise ValueError("MiniMax 配音文本为空。")
        if len(body) > 10000:
            raise ValueError(f"MiniMax 单段文本超过 10000 字符（实际 {len(body)}），请拆分后再生成。")
        api_key = load_minimax_api_key()
        payload: dict[str, Any] = {
            "model": model,
            "text": body,
            "stream": False,
            "voice_setting": {
                "voice_id": resolve_minimax_voice_id(voice_id),
                "speed": speed,
                "vol": volume,
                "pitch": pitch,
                "text_normalization": text_normalization,
            },
            "audio_setting": {
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "format": format,
                "channel": channel,
            },
        }
        if safe_text(emotion):
            payload["voice_setting"]["emotion"] = safe_text(emotion)
        last_error = ""
        for attempt, delay in enumerate((2, 5, 10), start=1):
            try:
                result = JsonHttpClient(timeout=180.0).post(
                    MINIMAX_T2A_URL,
                    json_payload=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < 3:
                    time.sleep(delay)
                    continue
                raise ValueError(f"MiniMax 配音请求失败：{last_error}") from exc
            if not isinstance(result, dict):
                raise ValueError(f"MiniMax 配音接口返回异常：{result}")
            base_resp = result.get("base_resp", {}) or {}
            code = base_resp.get("status_code")
            if code == 0:
                audio_hex = (result.get("data") or {}).get("audio", "")
                if not audio_hex:
                    raise ValueError("MiniMax 响应里没有 audio 字段。")
                final_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.write_bytes(bytes.fromhex(audio_hex))
                normalize_audio_loudness(final_path)
                return final_path
            last_error = f"[{code}] {base_resp.get('status_msg', '未知错误')}"
            if code in (1001, 1002, 1039) and attempt < 3:
                time.sleep(delay)
                continue
            raise ValueError(f"MiniMax 配音失败：{last_error}")
        raise ValueError(f"MiniMax 配音失败：{last_error}")

    def _finalize_generated_voice(self, generated_path: Path, final_path: Path) -> Path:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if generated_path.resolve() != final_path.resolve():
            generated_path.replace(final_path)
        normalize_generated_voice_silence(final_path)
        normalize_audio_loudness(final_path)
        return final_path

    def _has_existing_stale_voice_file(
        self,
        project_id: int,
        *,
        job: VoiceJob,
        account: dict[str, Any],
        identity: VoiceSynthesisIdentity,
    ) -> bool:
        account_label = safe_text(account.get("label"))
        current_hash = safe_text(job.block.get("text_hash"))
        current_fingerprint = identity.fingerprint(account_label=account_label, text_hash=current_hash)
        for asset in self.repo.asset_bindings(project_id):
            if asset["asset_type"] != "voice" or asset["status"] != "ready":
                continue
            if int(asset["script_block_id"] or 0) != int(job.block["id"]):
                continue
            if safe_text(asset.get("account_label")) != account_label:
                continue
            asset_hash = safe_text(asset.get("text_hash"))
            asset_fingerprint = safe_text(asset.get("generation_fingerprint"))
            if asset_hash == current_hash and asset_fingerprint == current_fingerprint:
                continue
            path_text = safe_text(asset.get("path"))
            if path_text and Path(path_text).exists():
                return True
        return False

    def _voice_filename(self, job: VoiceJob) -> str:
        label = safe_text(job.block.get("block_label")) or extract_label(safe_text(job.block.get("body")))
        if job.kind == "product":
            price = self._voice_price_label(job.price_label)
            parts = [price, job.uid, job.product_name, label]
        elif job.kind == "intro":
            parts = ["0", "引言", label]
        elif job.kind == "price_transition":
            parts = ["0", "价格", job.price_range_label or job.product_name, label]
        else:
            parts = [job.kind, job.uid, job.product_name, label]
        return safe_path_component("-".join(part for part in parts if part)) + ".wav"

    def _voice_price_label(self, value: str) -> str:
        number = first_number(value)
        if number is None:
            return safe_text(value)
        return str(int(number)) if number.is_integer() else str(number)

    def _upsert_voice_asset(
        self,
        project_id: int,
        *,
        job: VoiceJob,
        account: dict[str, Any],
        path: Path,
        identity: VoiceSynthesisIdentity,
    ) -> None:
        meta = file_metadata(path)
        ts = now_iso()
        account_label = safe_text(account.get("label"))
        account_id = safe_text(account.get("account_id"))
        generation_fingerprint = identity.fingerprint(
            account_label=account_label,
            text_hash=safe_text(job.block.get("text_hash")),
        )
        with self.db.connect() as conn:
            expiring = conn.execute(
                """
                SELECT * FROM asset_bindings
                WHERE project_id=? AND script_block_id=? AND asset_type='voice'
                  AND account_label=?
                  AND (COALESCE(text_hash, '')<>? OR COALESCE(generation_fingerprint, '')<>?)
                  AND source_kind<>'manual' AND status<>'expired'
                """,
                (
                    project_id,
                    job.block["id"],
                    account_label,
                    job.block["text_hash"],
                    generation_fingerprint,
                ),
            ).fetchall()
            existing = conn.execute(
                """
                SELECT * FROM asset_bindings
                WHERE project_id=? AND uid=? AND script_block_id=? AND asset_type='voice'
                  AND account_label=? AND block_label=? AND path=?
                """,
                (
                    project_id,
                    job.uid,
                    job.block["id"],
                    account_label,
                    safe_text(job.block.get("price_range_label")) if job.kind == "price_transition" else safe_text(job.block.get("block_label")),
                    str(path),
                ),
            ).fetchone()
            conn.execute(
                """
                UPDATE asset_bindings
                SET status='expired', updated_at=?
                WHERE project_id=? AND script_block_id=? AND asset_type='voice' AND account_label=?
                  AND (COALESCE(text_hash, '')<>? OR COALESCE(generation_fingerprint, '')<>?)
                """,
                (
                    ts,
                    project_id,
                    job.block["id"],
                    account_label,
                    job.block["text_hash"],
                    generation_fingerprint,
                ),
            )
            conn.execute(
                """
                INSERT INTO asset_bindings
                    (project_id, uid, script_block_id, asset_type, account_label, account_id, block_label, script_id, text_hash,
                     voice_provider, voice_model, voice_id, synthesis_settings_hash, generation_fingerprint,
                     path, status, source_kind, file_size, file_mtime, confirmed, created_at, updated_at)
                VALUES (?, ?, ?, 'voice', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', 'generated', ?, ?, 1, ?, ?)
                ON CONFLICT(project_id, uid, script_block_id, asset_type, account_label, block_label, path)
                DO UPDATE SET
                    account_id=excluded.account_id,
                    text_hash=excluded.text_hash,
                    voice_provider=excluded.voice_provider,
                    voice_model=excluded.voice_model,
                    voice_id=excluded.voice_id,
                    synthesis_settings_hash=excluded.synthesis_settings_hash,
                    generation_fingerprint=excluded.generation_fingerprint,
                    status='ready',
                    file_size=excluded.file_size,
                    file_mtime=excluded.file_mtime,
                    updated_at=excluded.updated_at
                """,
                (
                    project_id,
                    job.uid,
                    job.block["id"],
                    account_label,
                    account_id,
                    safe_text(job.block.get("price_range_label")) if job.kind == "price_transition" else safe_text(job.block.get("block_label")),
                    safe_text(job.block.get("script_id")) or f"script-{job.block['id']}",
                    safe_text(job.block.get("text_hash")),
                    identity.provider,
                    identity.model,
                    identity.voice_id,
                    identity.settings_hash,
                    generation_fingerprint,
                    str(path),
                    meta["file_size"],
                    meta["file_mtime"],
                    ts,
                    ts,
                ),
            )
            current = conn.execute(
                """
                SELECT * FROM asset_bindings
                WHERE project_id=? AND uid=? AND script_block_id=? AND asset_type='voice'
                  AND account_label=? AND block_label=? AND path=?
                """,
                (
                    project_id,
                    job.uid,
                    job.block["id"],
                    account_label,
                    safe_text(job.block.get("price_range_label")) if job.kind == "price_transition" else safe_text(job.block.get("block_label")),
                    str(path),
                ),
            ).fetchone()
            current_id = int(current["id"])
            for expired in expiring:
                if int(expired["id"]) == current_id or safe_text(expired["path"]) == str(path):
                    continue
                record_resource_state_event_in_connection(
                    conn,
                    project_id=project_id,
                    resource_kind="voice",
                    resource_key=f"asset_binding:{int(expired['id'])}",
                    path=safe_text(expired["path"]),
                    previous_state=safe_text(expired["status"]),
                    new_state="expired",
                    reason="voice_generation_identity_changed",
                    source="voice_generation",
                    account_label=account_label,
                    details={
                        "previous_text_hash": safe_text(expired["text_hash"]),
                        "text_hash": safe_text(job.block.get("text_hash")),
                        "generation_fingerprint": generation_fingerprint,
                    },
                    created_at=ts,
                )
                stale_path = safe_text(expired["path"])
                if stale_path:
                    register_cleanup_candidate_in_connection(
                        conn,
                        project_id=project_id,
                        resource_kind="asset_voice",
                        path=stale_path,
                        reason="voice_generation_identity_changed",
                        eligible_at=datetime.now(timezone.utc)
                        + timedelta(days=DERIVED_ASSET_RETENTION_DAYS),
                        details={
                            "asset_binding_id": int(expired["id"]),
                            "account_label": account_label,
                            "discovered_by": "voice_generation",
                        },
                    )
            record_resource_state_event_in_connection(
                conn,
                project_id=project_id,
                resource_kind="voice",
                resource_key=f"asset_binding:{current_id}",
                path=path,
                previous_state=safe_text(existing["status"]) if existing else "",
                new_state="created" if existing is None else "updated",
                reason="voice_generated",
                source="voice_generation",
                account_label=account_label,
                details={
                    "script_block_id": int(job.block["id"]),
                    "text_hash": safe_text(job.block.get("text_hash")),
                    "generation_fingerprint": generation_fingerprint,
                    "provider": identity.provider,
                },
                created_at=ts,
            )

    def _ensure_tts_api_ready(
        self,
        http: "JsonHttpClient",
        *,
        logs: list[str],
        start_if_needed: bool = True,
        progress_hook: Callable[[str], None] | None = None,
    ) -> None:
        def emit(message: str) -> None:
            logs.append(message)
            if progress_hook:
                progress_hook(message)

        emit(f"[服务检查] 检查 IndexTTS：{DEFAULT_TTS_API_BASE_URL}")
        health = self._api_health(http)
        if health is None:
            if not start_if_needed:
                raise ValueError("本地 IndexTTS2 服务未启动。")
            emit("[服务检查] 服务未启动，正在尝试自动启动。")
            self._launch_tts_api()
            deadline = time.time() + 90
            while time.time() < deadline:
                time.sleep(2)
                health = self._api_health(http)
                if health is not None:
                    break
        if health is None:
            raise ValueError("本地 IndexTTS2 API 启动失败，健康检查不可用。")
        loaded = http.post(f"{DEFAULT_TTS_API_BASE_URL.rstrip('/')}/v1/model/load")
        if not isinstance(loaded, dict) or not loaded.get("loaded"):
            raise ValueError("本地配音模型预热失败。")
        emit("[服务检查] 配音服务已就绪。")

    def _ensure_registered_voice(
        self,
        http: "JsonHttpClient",
        *,
        voice_id: str,
        account: dict[str, Any],
        logs: list[str],
        progress_hook: Callable[[str], None] | None = None,
    ) -> None:
        def emit(message: str) -> None:
            logs.append(message)
            if progress_hook:
                progress_hook(message)

        profile = self._voice_profile(voice_id)
        reference = Path(safe_text(profile.get("speaker_audio_path")))
        if not reference.exists():
            return
        payload = {
            "voice_id": voice_id,
            "display_name": safe_text(account.get("voice_name") or profile.get("display_name") or voice_id),
            "speaker_audio_path": str(reference),
            "overwrite": True,
        }
        result = http.post(f"{DEFAULT_TTS_API_BASE_URL.rstrip('/')}/v1/voices/register/path", json_payload=payload)
        if not isinstance(result, dict) or not safe_text(result.get("voice_id")):
            raise ValueError(f"音色注册接口返回异常：{result}")
        emit(f"[音色注册] 已确认音色：{voice_id}")

    def _api_health(self, http: "JsonHttpClient") -> dict[str, Any] | None:
        try:
            payload = http.get(f"{DEFAULT_TTS_API_BASE_URL.rstrip('/')}/health")
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _launch_tts_api(self) -> None:
        python_path = DEFAULT_INDEXTTS_DIR / "wzf310" / "python.exe"
        api_server_path = DEFAULT_INDEXTTS_DIR / "api_server.py"
        if not python_path.exists() or not api_server_path.exists():
            raise ValueError("IndexTTS2 API 启动文件不存在，无法自动拉起本地配音服务。")
        env = os.environ.copy()
        env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        env["HF_HOME"] = str(DEFAULT_INDEXTTS_DIR / "hf_download")
        env["HF_HUB_CACHE"] = str(DEFAULT_INDEXTTS_DIR / "hf_download" / "hub")
        env["TRANSFORMERS_CACHE"] = str(DEFAULT_INDEXTTS_DIR / "hf_download" / "hub")
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_DATASETS_OFFLINE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env["PATH"] = (
            f"{DEFAULT_INDEXTTS_DIR / 'wzf310'};"
            f"{DEFAULT_INDEXTTS_DIR / 'wzf310' / 'Scripts'};"
            f"{DEFAULT_INDEXTTS_DIR / 'wzf310' / 'ffmpeg' / 'bin'};"
            + env.get("PATH", "")
        )
        log_dir = DEFAULT_INDEXTTS_DIR / "outputs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = (log_dir / "bworkflow-sql-api-server.stdout.log").open("ab")
        stderr_log = (log_dir / "bworkflow-sql-api-server.stderr.log").open("ab")
        self._tts_log_handles = [stdout_log, stderr_log]
        subprocess.Popen(
            [str(python_path), "-s", str(api_server_path)],
            cwd=str(DEFAULT_INDEXTTS_DIR),
            env=env,
            stdout=stdout_log,
            stderr=stderr_log,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

    def _find_tts_service_pids(self) -> list[str]:
        completed = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="ignore",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pids: list[str] = []
        for raw_line in completed.stdout.splitlines():
            line = raw_line.strip()
            if not line or f":{urllib.parse.urlparse(DEFAULT_TTS_API_BASE_URL).port}" not in line:
                continue
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if parts:
                pid = parts[-1].strip()
                if pid.isdigit():
                    pids.append(pid)
        return list(dict.fromkeys(pids))

    def _validate_product_order_strategy(self, product_order_strategy: str) -> str:
        order_strategy = safe_text(product_order_strategy) or DEFAULT_PRODUCT_ORDER_STRATEGY
        if order_strategy not in SUPPORTED_PRODUCT_ORDER_STRATEGIES:
            raise ValueError(f"unsupported product_order_strategy: {order_strategy}")
        return order_strategy

    def _ordered_products(
        self,
        project_id: int,
        *,
        mode: str,
        top_uids: list[str],
        product_uids: list[str],
        product_order_strategy: str = DEFAULT_PRODUCT_ORDER_STRATEGY,
    ) -> list[dict[str, Any]]:
        products = self.repo.products(project_id, include_removed=False)
        explicit_rank = {uid.casefold(): index for index, uid in enumerate(product_uids)}
        if explicit_rank:
            products = sorted(
                (product for product in products if product["uid"].casefold() in explicit_rank),
                key=lambda product: explicit_rank[product["uid"].casefold()],
            )
        order_strategy = self._validate_product_order_strategy(product_order_strategy)
        if order_strategy == "price_segment_shuffle" and not explicit_rank:
            price_blocks = [block for block in self.repo.script_blocks(project_id) if block["script_type"] == "price_transition"]
            if _has_matching_price_groups(products, price_blocks):
                products = self._products_in_price_segment_shuffle_order(products, price_blocks)
        if mode != "top" or not top_uids:
            return products
        rank = {uid.casefold(): index for index, uid in enumerate(top_uids)}
        existing_order = {safe_text(item.get("uid")).casefold(): index for index, item in enumerate(products)}
        return sorted(
            products,
            key=lambda item: (
                0,
                rank[safe_text(item.get("uid")).casefold()],
            )
            if safe_text(item.get("uid")).casefold() in rank
            else (1, existing_order.get(safe_text(item.get("uid")).casefold(), int(item.get("sort_order") or 0))),
        )

    def _products_in_price_segment_shuffle_order(self, products: list[dict[str, Any]], price_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        product_segments = {safe_text(product.get("uid")): {"uid": safe_text(product.get("uid"))} for product in products}
        price_segments = {
            safe_text(block.get("price_range_label")): {"price_label": safe_text(block.get("price_range_label"))}
            for block in price_blocks
        }
        arranged = _arrange_price_segment_shuffle(
            products,
            price_blocks=price_blocks,
            price_segments=price_segments,
            product_segments=product_segments,
        )
        by_uid = {safe_text(product.get("uid")): product for product in products}
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for segment in arranged:
            uid = safe_text(segment.get("uid"))
            if uid and uid in by_uid and uid not in seen:
                ordered.append(by_uid[uid])
                seen.add(uid)
        ordered.extend([product for product in products if safe_text(product.get("uid")) not in seen])
        return ordered

    def _assemble_cli_command(
        self,
        project_id: int,
        *,
        account_label: str,
        intro_index: int,
        mode: str,
        top_uids: list[str],
        product_uids: list[str],
        product_order_strategy: str,
    ) -> str:
        parts = [
            "python",
            "-m",
            "bworkflow_sql",
            "assemble",
            str(project_id),
            "--account",
            account_label,
            "--intro-index",
            str(max(1, int(intro_index or 1))),
            "--mode",
            "top" if mode == "top" else "standard",
            "--product-order-strategy",
            product_order_strategy,
        ]
        if top_uids:
            parts.extend(["--top-uids", ",".join(top_uids)])
        if product_uids:
            parts.extend(["--product-uids", ",".join(product_uids)])
        return " ".join(parts)

    def _voice_scope_fragment(self, project: dict[str, Any], account_label: str) -> str:
        root = safe_text(project.get("voice_root")) or DEFAULT_OUTPUT_ROOT
        return str(voice_user_dir(root, project, account_label)) if safe_text(account_label) else ""

    def _manifest_entry(
        self,
        *,
        order: int,
        entry_type: str,
        section: str,
        block: dict[str, Any],
        account_label: str,
        account_id: str,
        assets: list[dict[str, Any]],
        product: dict[str, Any],
        source_label: str,
        display_template: str = "",
        preferred_voice_path_contains: str = "",
    ) -> dict[str, Any]:
        uid = safe_text(product.get("uid") or ("INTRO" if block["script_type"] == "intro" else "PRICE_TRANSITION"))
        voice = None
        if preferred_voice_path_contains:
            voice = self._ready_asset(
                assets,
                asset_type="voice",
                uid=uid,
                account_label=account_label,
                script_block_id=int(block["id"]),
                text_hash=safe_text(block["text_hash"]),
                path_contains=preferred_voice_path_contains,
            )
        if not voice:
            voice = self._ready_asset(assets, asset_type="voice", uid=uid, account_label=account_label, script_block_id=int(block["id"]), text_hash=safe_text(block["text_hash"]))
        if not voice and uid in {"INTRO", "PRICE_TRANSITION"}:
            shared_label = safe_text(block.get("block_label")) if uid == "INTRO" else safe_text(block.get("price_range_label"))
            if preferred_voice_path_contains:
                voice = self._ready_asset(
                    assets,
                    asset_type="voice",
                    uid=uid,
                    account_label=account_label,
                    text_hash=safe_text(block["text_hash"]),
                    block_label=shared_label,
                    path_contains=preferred_voice_path_contains,
                )
            if not voice:
                voice = self._ready_asset(
                    assets,
                    asset_type="voice",
                    uid=uid,
                    account_label=account_label,
                    text_hash=safe_text(block["text_hash"]),
                    block_label=shared_label,
                )
        template_suffix = image_set_for_template(display_template)
        display_user = user_for_template(display_template)
        image = None
        if product:
            if display_template:
                image = self._ready_asset(assets, asset_type="image", uid=uid, account_label=display_user, path_contains=template_suffix)
            else:
                image = self._ready_asset(assets, asset_type="image", uid=uid, account_label=display_user)
        video = self._ready_asset(assets, asset_type="video", uid=uid) if product else None
        video_slot = None
        if video:
            from .template_config import get_template_slot
            if display_template:
                try:
                    video_slot = get_template_slot(display_template)
                except ValueError:
                    video_slot = DEFAULT_DISPLAY_VIDEO_SLOT
            else:
                video_slot = DEFAULT_DISPLAY_VIDEO_SLOT
        return {
            "type": entry_type,
            "order_index": order,
            "section": section,
            "section_order": order,
            "product_uid": uid,
            "product_name": safe_text(product.get("title") or source_label),
            "price_label": safe_text(product.get("price_label")),
            "price_range_label": safe_text(block.get("price_range_label")),
            "source_label": source_label,
            "text": safe_text(block.get("body")),
            "audio_path": safe_text(voice.get("path")) if voice else "",
            "image_path": safe_text(image.get("path")) if image else "",
            "video_path": safe_text(video.get("path")) if video else "",
            "display_video_path": safe_text(video.get("path")) if video else "",
            "display_video_slot": video_slot,
            "binding_id": f"db:{block['id']}:{account_label}",
            "script_id": safe_text(block.get("script_id")) or f"script-{block['id']}",
            "account_id": account_id,
            "account_label": account_label,
            "text_hash": safe_text(block.get("text_hash")),
        }

    def _closing_manifest_entry(
        self,
        *,
        order: int,
        text: str,
        account: dict[str, Any],
        account_label: str,
        account_id: str,
    ) -> dict[str, Any]:
        audio_path = safe_text(account.get("closing_audio_path"))
        return {
            "type": "closing",
            "order_index": order,
            "section": "closing",
            "section_order": order,
            "product_uid": "CLOSING",
            "product_name": "结尾",
            "price_label": "",
            "price_range_label": "",
            "source_label": "固定结尾",
            "text": safe_text(text),
            "audio_path": audio_path if audio_path and Path(audio_path).exists() else "",
            "image_path": "",
            "video_path": "",
            "binding_id": f"closing:{account_label}",
            "script_id": "closing-fixed",
            "account_id": account_id,
            "account_label": account_label,
            "text_hash": "",
        }

    def _ready_asset(
        self,
        assets: list[dict[str, Any]],
        *,
        asset_type: str,
        uid: str = "",
        account_label: str = "",
        script_block_id: int = 0,
        text_hash: str = "",
        block_label: str = "",
        path_contains: str = "",
    ) -> dict[str, Any] | None:
        for asset in assets:
            if asset["asset_type"] != asset_type or asset["status"] != "ready":
                continue
            if uid and safe_text(asset.get("uid")) != uid:
                continue
            if account_label and safe_text(asset.get("account_label")) != account_label:
                continue
            if block_label and safe_text(asset.get("block_label")) != block_label:
                continue
            if path_contains and path_contains not in safe_text(asset.get("path")):
                continue
            asset_script_block_id = int(asset.get("script_block_id") or 0)
            if script_block_id and asset_script_block_id not in {0, script_block_id}:
                continue
            if text_hash and safe_text(asset.get("text_hash")) != text_hash:
                continue
            if safe_text(asset.get("path")) and Path(safe_text(asset.get("path"))).exists():
                return asset
        return None

    def _matching_price_blocks(self, product: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        price = first_number(safe_text(product.get("price_label")))
        if price is None:
            return []
        matched = [block for block in blocks if price_in_range(price, safe_text(block.get("price_range_label")))]
        return [random.choice(matched)] if matched else []

    def _matching_price_block(self, product: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
        matched = self._matching_price_blocks(product, blocks)
        return matched[0] if matched else None

    def _voice_ready_for_block(
        self,
        assets: list[dict[str, Any]],
        *,
        uid: str,
        block: dict[str, Any],
        account_label: str,
        block_label: str = "",
    ) -> bool:
        exact = self._ready_asset(
            assets,
            asset_type="voice",
            uid=uid,
            account_label=account_label,
            script_block_id=int(block.get("id") or 0),
            text_hash=safe_text(block.get("text_hash")),
        )
        if exact:
            return True
        return bool(
            block_label
            and any(
                asset["asset_type"] == "voice"
                and asset["status"] == "ready"
                and safe_text(asset.get("uid")) == uid
                and (not account_label or safe_text(asset.get("account_label")) == account_label)
                and safe_text(asset.get("block_label")) == block_label
                and safe_text(asset.get("text_hash")) == safe_text(block.get("text_hash"))
                and Path(safe_text(asset.get("path"))).exists()
                for asset in assets
            )
        )

    def _choose_voice_ready_block(
        self,
        blocks: list[dict[str, Any]],
        assets: list[dict[str, Any]],
        *,
        uid: str,
        account_label: str,
        block_label: str = "",
    ) -> dict[str, Any] | None:
        if not blocks:
            return None
        ready = [
            block
            for block in blocks
            if self._voice_ready_for_block(
                assets,
                uid=uid,
                block=block,
                account_label=account_label,
                block_label=block_label,
            )
        ]
        return random.choice(ready) if ready else None

    def _missing_voice_issue(
        self,
        project_id: int,
        *,
        account_label: str,
        block: dict[str, Any],
        uid: str,
        product: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "level": "error",
            "code": "missing_voice_asset",
            "message": "current script block has no ready voice asset with matching text_hash",
            "script_type": safe_text(block.get("script_type")),
            "script_block_id": int(block.get("id") or 0),
            "script_id": safe_text(block.get("script_id")),
            "uid": safe_text(uid),
            "owner_uid": safe_text(block.get("owner_uid")),
            "product_name": safe_text(product.get("title")),
            "block_label": safe_text(block.get("block_label")),
            "price_range_label": safe_text(block.get("price_range_label")),
            "account_label": account_label,
            "text_hash": safe_text(block.get("text_hash")),
            "command": f"python -m bworkflow_sql voice-counts {project_id} --account {account_label}",
            "follow_up_command": f"python -m bworkflow_sql voice {project_id} --account {account_label}",
        }

    def _raise_if_missing_voice(
        self,
        project_id: int,
        *,
        account_label: str,
        block: dict[str, Any],
        assets: list[dict[str, Any]],
        uid: str,
        product: dict[str, Any],
        block_label: str = "",
    ) -> None:
        if self._voice_ready_for_block(
            assets,
            uid=uid,
            block=block,
            account_label=account_label,
            block_label=block_label,
        ):
            return
        issue = self._missing_voice_issue(project_id, account_label=account_label, block=block, uid=uid, product=product)
        raise ValueError(
            "missing_voice_asset: current text_hash has no ready voice asset. "
            f"script_type={issue['script_type']} uid={issue['uid']} block_label={issue['block_label']} "
            f"text_hash={issue['text_hash']}. "
            f"Run `{issue['command']}` then `{issue['follow_up_command']}`."
        )

    def _append_spoken_paragraph(self, lines: list[str], text: str) -> None:
        body = safe_text(text).strip()
        if not body:
            return
        if lines:
            lines.append("")
        lines.append(body)


def render_segment_counts(segments: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(segments, list):
        return counts
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_type = safe_text(segment.get("type"))
        if segment_type:
            counts[segment_type] = counts.get(segment_type, 0) + 1
    return counts


def _assembly_plan_entry(
    order: int,
    section: str,
    block: dict[str, Any],
    *,
    product: dict[str, Any] | None = None,
) -> dict[str, Any]:
    product = product or {}
    return {
        "order": order,
        "section": section,
        "script_type": safe_text(block.get("script_type")),
        "script_block_id": int(block.get("id") or 0),
        "script_id": safe_text(block.get("script_id")),
        "owner_uid": safe_text(block.get("owner_uid")),
        "product_uid": safe_text(product.get("uid")),
        "product_name": safe_text(product.get("title")),
        "price_range_label": safe_text(block.get("price_range_label")),
        "block_label": safe_text(block.get("block_label")),
        "text_hash": safe_text(block.get("text_hash")),
    }


def render_package_next_step(
    *,
    project_id: int,
    account_label: str,
    output_mode: str,
    package_path: Path,
    jianying_manifest_path: Path | None = None,
) -> dict[str, Any]:
    if output_mode == "final_mp4":
        target_mp4 = package_path.with_suffix(".mp4")
        return {
            "mode": "final_mp4",
            "status": "ready",
            "action": "render_final_video",
            "target_mp4": str(target_mp4),
        }
    manifest_path = jianying_manifest_path or package_path.with_suffix(".jianying.manifest.json")
    return {
        "mode": "jianying_draft",
        "status": "ready",
        "message": "Jianying manifest has been generated from the RenderPackage.",
        "package_path": str(package_path),
        "manifest_path": str(manifest_path),
        "project_id": project_id,
        "account": account_label,
        "command": (
            f"python -m bworkflow_sql jianying {project_id} "
            f"--manifest {manifest_path} --draft-name {safe_path_component(account_label or 'render-package')}"
        ),
    }


def render_package_stale_product_image_next_step(
    stale_product_images: list[dict[str, Any]],
    *,
    project_id: int,
    account_label: str,
    product_card_template_id: str = "",
) -> dict[str, Any]:
    stale_command = (
        f"python -m bworkflow_sql product-images {project_id} "
        f"--account {account_label} --mode stale"
    )
    template_arg = safe_text(product_card_template_id)
    if template_arg:
        stale_command = f"{stale_command} --product-card-template-id {template_arg}"
    if len(stale_product_images) == 1:
        uid = safe_text(stale_product_images[0].get("uid"))
        if uid:
            stale_command = f"{stale_command} --product-uid {uid}"
    all_command = (
        f"python -m bworkflow_sql product-images {project_id} "
        f"--account {account_label} --mode all"
    )
    if template_arg:
        all_command = f"{all_command} --product-card-template-id {template_arg}"
    return {
        "mode": "product_image_stale_review",
        "status": "confirmation_required",
        "message": "检测到商品数据变了，是否重生成商品图？",
        "stale_count": len(stale_product_images),
        "options": [
            {
                "id": "reuse",
                "label": "继续复用旧商品图",
                "command_hint": "--stale-product-image-policy reuse",
            },
            {
                "id": "regenerate_stale",
                "label": "先重生成过期商品图，再重新生成 RenderPackage",
                "command_hint": stale_command,
            },
            {
                "id": "regenerate_all",
                "label": "重生成全部商品图，再重新生成 RenderPackage",
                "command_hint": all_command,
            },
        ],
    }


def _workflow_doctor_issues(source: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        item = dict(issue)
        item.setdefault("source", source)
        item["source"] = source
        result.append(item)
    return result


def _phase7_selection_context(products: list[dict[str, Any]]) -> dict[str, Any]:
    featured_products: list[dict[str, str]] = []
    for product in products:
        try:
            product_card = json.loads(safe_text(product.get("product_card_json")) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            product_card = {}
        if not isinstance(product_card, dict) or product_card.get("featured") is not True:
            continue
        uid = safe_text(product.get("uid"))
        title = safe_text(product.get("title"))
        if uid and title:
            featured_products.append({"uid": uid, "title": title})
    return {
        "featured_count": len(featured_products),
        "featured_products": featured_products,
    }


def _workflow_doctor_payload(
    *,
    project_id: int,
    project: dict[str, Any],
    account_label: str,
    ok: bool,
    status: str,
    blocked_by: str,
    checks: dict[str, Any],
    issues: list[dict[str, Any]],
    next_hint: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "blocked_by": blocked_by or None,
        "project": {
            "id": int(project_id),
            "name": safe_text(project.get("name")),
            "category": safe_text(project.get("category_name") or project.get("category")),
            "scheme_id": safe_text(project.get("scheme_id")),
            "scheme_name": safe_text(project.get("scheme_name")),
        },
        "account": account_label,
        "checks": checks,
        "issues": issues,
        "next": next_hint or {},
    }


def _normalize_project_lookup_text(value: Any) -> str:
    return re.sub(r"\s+", "", safe_text(value)).casefold()


def _project_ref_matches(normalized_ref: str, value: Any) -> bool:
    normalized_value = _normalize_project_lookup_text(value)
    return bool(
        normalized_ref
        and normalized_value
        and (normalized_ref == normalized_value or normalized_ref in normalized_value)
    )


def render_package_to_jianying_manifest(
    package: dict[str, Any],
    output_path: str | Path,
    *,
    project_id: int,
    account_label: str,
    display_template: str = "",
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    effective_display_template = render_package_display_template(
        package,
        account_label=account_label,
        display_template=display_template,
    )
    package_output = package.get("output") if isinstance(package.get("output"), dict) else {}
    package_product_media_mode = (
        safe_text(package_output.get("productMediaMode")) or DEFAULT_PRODUCT_MEDIA_MODE
    )
    for order, segment in enumerate(package.get("segments") or [], start=1):
        if not isinstance(segment, dict):
            continue
        segment_type = safe_text(segment.get("type"))
        if segment_type == "price_transition":
            label = safe_text(segment.get("priceRangeLabel"))
            entries.append(
                {
                    "type": "transition",
                    "order_index": order,
                    "section": "price_transition",
                    "section_order": order,
                    "product_uid": "PRICE_TRANSITION",
                    "product_name": label or "Price transition",
                    "price_label": "",
                    "price_range_label": label,
                    "source_label": f"Price transition {label}".strip(),
                    "text": safe_text(segment.get("transitionText")),
                    "audio_path": safe_text(segment.get("voiceAsset")),
                    "image_path": "",
                    "video_path": "",
                    "display_video_path": "",
                    "display_video_slot": None,
                    "binding_id": safe_text(segment.get("id")) or f"price:{order}",
                    "script_id": str(segment.get("sourceScriptBlockId") or ""),
                    "account_id": "",
                    "account_label": account_label,
                    "text_hash": "",
                }
            )
        elif segment_type == "product_recommendation":
            segment_product_media_mode = safe_text(segment.get("productMediaMode")) or package_product_media_mode
            video_path = (
                safe_text(segment.get("videoAsset"))
                if segment_product_media_mode == "video_preferred"
                else ""
            )
            entries.append(
                {
                    "type": "product",
                    "order_index": order,
                    "section": "product",
                    "section_order": order,
                    "product_uid": safe_text(segment.get("productUid")),
                    "product_name": safe_text(segment.get("productTitle")),
                    "price_label": safe_text(segment.get("priceRangeLabel")),
                    "price_range_label": safe_text(segment.get("priceRangeLabel")),
                    "source_label": safe_text(segment.get("productTitle")),
                    "text": safe_text(segment.get("spokenText")),
                    "audio_path": safe_text(segment.get("voiceAsset")),
                    "image_path": safe_text(segment.get("imageCardAsset")),
                    "video_path": video_path,
                    "display_video_path": video_path,
                    "display_video_slot": render_package_display_video_slot(
                        effective_display_template
                    )
                    if video_path
                    else None,
                    "binding_id": safe_text(segment.get("id"))
                    or f"product:{safe_text(segment.get('productUid'))}",
                    "script_id": str(segment.get("sourceScriptBlockId") or ""),
                    "account_id": "",
                    "account_label": account_label,
                    "text_hash": "",
                    "subtitles": segment.get("subtitles") or [],
                }
            )
    manifest = {
        "source": "render_package",
        "project_id": project_id,
        "project": package.get("project") or {},
        "account_label": account_label,
        "display_template": effective_display_template,
        "created_at": now_iso(),
        "entries": entries,
    }
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return output


def ensure_template_calibration_placeholder_audio(
    path: str | Path,
    *,
    duration_seconds: float = 8.0,
) -> Path:
    target = Path(path)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 48000
    frame_count = max(1, int(sample_rate * duration_seconds))
    with wave.open(str(target), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x00\x00" * frame_count)
    return target


def build_template_calibration_probe_manifest_from_assets(
    *,
    project: dict[str, Any],
    products: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    account_label: str,
    product_uid: str,
    product_media_mode: str = DEFAULT_PRODUCT_MEDIA_MODE,
    product_card_template_id: str = "",
    placeholder_audio_path: str | Path,
) -> dict[str, Any]:
    account = safe_text(account_label)
    uid = safe_text(product_uid)
    if not uid:
        raise ValueError("calibration product_uid cannot be empty")
    if safe_text(product_media_mode) != "video_preferred":
        raise ValueError("template calibration requires product_media_mode=video_preferred")
    selected_template = resolve_product_card_template(
        account,
        product_card_template_id,
        require_explicit=True,
    )
    template_id = safe_text(selected_template.get("templateId")) or safe_text(product_card_template_id)
    display_template = (
        safe_text(selected_template.get("displayName"))
        or display_template_for_product_card_template_id(template_id)
    )
    selected_image_set = image_set_for_template(display_template)
    product = _calibration_product(products, uid)
    image = _calibration_ready_asset(
        assets,
        asset_type="image",
        uid=uid,
        account_label=account,
        preferred_image_set=selected_image_set,
    )
    if not image:
        raise ValueError(f"calibration product {uid} has no ready product image for {account}/{selected_image_set}")
    video = _calibration_ready_asset(assets, asset_type="video", uid=uid, allow_unscoped_account=True)
    if not video:
        raise ValueError(f"calibration product {uid} has no ready display_video_path")

    image_path = _calibration_existing_path(image.get("path"), label=f"{uid} image")
    video_path = _calibration_existing_path(video.get("path"), label=f"{uid} video")
    product_card = product_card_payload_for_product(
        product,
        project=project,
        fallback_image_path=image_path,
        account_label=account,
        product_card_template_id=template_id,
    )
    slot = render_package_display_video_slot(display_template)
    if product_card:
        slot["templateId"] = safe_text(product_card.get("templateId")) or template_id
        slot["templateVersion"] = safe_text(product_card.get("templateVersion")) or safe_text(slot.get("templateVersion"))
    entry = {
        "type": "product",
        "order_index": 1,
        "section": "product",
        "section_order": 1,
        "product_uid": uid,
        "product_name": safe_text(product.get("title")),
        "price_label": safe_text(product.get("price_label")),
        "price_range_label": safe_text(product.get("price_label")),
        "source_label": safe_text(product.get("title")),
        "text": "Template calibration placeholder audio.",
        "audio_path": str(Path(placeholder_audio_path)),
        "image_path": str(image_path),
        "video_path": str(video_path),
        "display_video_path": str(video_path),
        "display_video_slot": slot,
        "binding_id": f"template-calibration:{uid}",
        "script_id": "template-calibration",
        "account_id": "",
        "account_label": account,
        "text_hash": "",
    }
    if product_card:
        entry["product_card"] = product_card
    return {
        "source": "template_calibration_assets",
        "mode": "template_calibration_probe",
        "project_id": int(project.get("id") or project.get("project_id") or 0),
        "project": {
            "category": safe_text(project.get("category_name") or project.get("name")),
            "account": account,
            "bworkflowProjectId": int(project.get("id") or project.get("project_id") or 0),
        },
        "account_label": account,
        "display_template": display_template,
        "created_at": now_iso(),
        "probe_note": "Single-product display-video template calibration probe.",
        "entries": [entry],
    }


def _calibration_product(products: list[dict[str, Any]], uid: str) -> dict[str, Any]:
    matches = [
        product
        for product in products
        if safe_text(product.get("uid")).casefold() == safe_text(uid).casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"expected 1 calibration product {uid}, got {len(matches)}")
    return matches[0]


def _calibration_ready_asset(
    assets: list[dict[str, Any]],
    *,
    asset_type: str,
    uid: str,
    account_label: str = "",
    preferred_image_set: str = "",
    allow_unscoped_account: bool = False,
) -> dict[str, Any] | None:
    preferred = safe_text(preferred_image_set)
    candidates: list[dict[str, Any]] = []
    for asset in assets:
        if safe_text(asset.get("asset_type")) != asset_type:
            continue
        if safe_text(asset.get("status")) != "ready":
            continue
        if safe_text(asset.get("uid")).casefold() != safe_text(uid).casefold():
            continue
        asset_account = safe_text(asset.get("account_label"))
        if account_label and asset_account != account_label:
            if not (allow_unscoped_account and not asset_account):
                continue
        path_text = safe_text(asset.get("path"))
        if not path_text or not Path(path_text).is_file():
            continue
        candidates.append(asset)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            not _calibration_path_has_part(Path(safe_text(item.get("path"))), preferred)
            if asset_type == "image" and preferred
            else False,
            safe_text(item.get("account_label")) != account_label,
            safe_text(item.get("path")),
        ),
    )[0]


def _calibration_existing_path(value: Any, *, label: str) -> Path:
    path = Path(safe_text(value))
    if not path.is_absolute():
        path = path.resolve()
    if not path.is_file():
        raise ValueError(f"calibration {label} path does not exist: {path}")
    return path


def _calibration_path_has_part(path: Path, part: str) -> bool:
    needle = safe_text(part)
    return not needle or any(safe_text(value) == needle for value in path.parts)


def build_template_calibration_probe_manifest(
    manifest: dict[str, Any],
    *,
    product_uid: str,
    created_from: str = "",
) -> dict[str, Any]:
    uid = safe_text(product_uid)
    if not uid:
        raise ValueError("校准商品 UID 不能为空。")
    entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and safe_text(entry.get("type")) == "product"
        and safe_text(entry.get("product_uid")).casefold() == uid.casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"期望找到 1 条商品 {uid}，实际找到 {len(matches)} 条。")
    selected = json.loads(json.dumps(matches[0], ensure_ascii=False, default=str))
    if not safe_text(selected.get("display_video_path")):
        raise ValueError(f"商品 {uid} 没有 display_video_path，不能校准商品视频位置。")
    if not isinstance(selected.get("display_video_slot"), dict):
        raise ValueError(f"商品 {uid} 没有 display_video_slot，不能校准商品视频位置。")
    selected["order_index"] = 1
    selected["section_order"] = 1

    probe = json.loads(json.dumps(manifest, ensure_ascii=False, default=str))
    probe["entries"] = [selected]
    probe["mode"] = "template_calibration_probe"
    if created_from:
        probe["created_from"] = created_from
    probe["probe_note"] = "Single-product display-video template calibration probe."
    return probe


def render_package_display_template(
    package: dict[str, Any],
    *,
    account_label: str,
    display_template: str = "",
) -> str:
    explicit = safe_text(display_template)
    if explicit:
        return explicit
    for segment in package.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        product_card = segment.get("productCard")
        if isinstance(product_card, dict):
            mapped = display_template_for_product_card_template_id(
                safe_text(product_card.get("templateId"))
            )
            if mapped:
                return mapped
        from_path = display_template_from_image_path(
            safe_text(segment.get("imageCardAsset")),
            account_label=account_label,
        )
        if from_path:
            return from_path
    return ""


def render_package_display_video_slot(display_template: str = "") -> dict[str, Any]:
    if display_template:
        try:
            from .template_config import get_template_slot

            return get_template_slot(display_template)
        except ValueError:
            pass
    return dict(DEFAULT_DISPLAY_VIDEO_SLOT)


def run_subprocess_text(cmd: list[str]) -> WorkflowRunResult:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    completed = subprocess.run(cmd, capture_output=True, env=env)
    return WorkflowRunResult(
        args=cmd,
        returncode=completed.returncode,
        stdout=decode_process_output(completed.stdout),
        stderr=decode_process_output(completed.stderr),
    )


def decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    encodings = ["utf-8-sig", "utf-8", locale.getpreferredencoding(False), "gb18030", "mbcs"]
    for encoding in dict.fromkeys(item for item in encodings if item):
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


class JsonHttpClient:
    def __init__(self, timeout: float = 60.0, retries: int = 3) -> None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        self.timeout = timeout
        self._session = requests.Session()
        self._requests = requests
        retry = Retry(total=retries, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = self._session.request(
                method.upper(),
                url,
                params=params,
                json=json_payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except self._requests.exceptions.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            code = exc.response.status_code if exc.response is not None else 0
            raise ValueError(f"HTTP {code}: {detail}") from exc
        except self._requests.exceptions.ConnectionError as exc:
            raise ValueError(f"网络连接失败: {exc}") from exc
        except self._requests.exceptions.Timeout as exc:
            raise ValueError(f"请求超时: {exc}") from exc
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", url, params=params)

    def post(self, url: str, *, json_payload: Any | None = None, headers: dict[str, str] | None = None) -> Any:
        return self.request("POST", url, json_payload=json_payload, headers=headers)


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，]+", value or "") if item.strip()]


def safe_path_component(value: str) -> str:
    text = safe_text(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "B-Workflow-SQL"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"无法生成不重名文件：{path}")


def standalone_voice_filename(*, voice_label: str, source_label: str = "", text: str = "") -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    source = safe_text(source_label) or extract_label(text, length=12) or "粘贴文本"
    parts = ["单独配音", voice_label, source, timestamp]
    return safe_path_component("-".join(part for part in parts if safe_text(part))) + ".wav"










def extract_label(text: str, length: int = 2) -> str:
    clean = re.sub(r"\s+", "", safe_text(text))
    clean = re.sub(r"[，。！？、,.!?;；:\"“”'‘’（）()【】\[\]]", "", clean)
    return clean[:length] or "正文"


def first_number(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def price_in_range(price: float, label: str) -> bool:
    try:
        numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", label)]
    except (ValueError, OverflowError):
        return False
    if not numbers:
        return False
    if len(numbers) == 1:
        if any(token in label for token in ("以上", "+", "up")):
            return price >= numbers[0]
        if any(token in label for token in ("以下", "以内", "under")):
            return price <= numbers[0]
        return abs(price - numbers[0]) < 0.001
    low, high = min(numbers[0], numbers[1]), max(numbers[0], numbers[1])
    return low <= price <= high
