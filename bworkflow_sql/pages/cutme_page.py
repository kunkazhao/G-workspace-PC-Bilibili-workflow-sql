from __future__ import annotations

import json
import os
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

import customtkinter as ctk

from ..asset_paths import project_category_folder, voice_user_dir
from ..components import (
    AppCard,
    AppComboBox,
    AppEntry,
    AppLabel,
    AppTextbox,
    BasePage,
    DangerButton,
    FormRow,
    GhostButton,
    NavButton,
    PrimaryButton,
)
from ..copy_writer import preview_copy_write, write_copy_blocks_to_markdown
from ..db import Database
from ..legacy_import import LegacyImportService
from ..master_data import MasterDataService, display_name
from ..master_service import MasterServiceManager, is_master_connection_error
from ..outline_service import OutlineService
from ..repositories import Repository
from ..settings import (
    CUTME_OUTPUT_ROOT,
    CUTME_ROOT,
    DB_PATH,
    DEFAULT_IMAGE_ROOT,
    DEFAULT_JIANYING_DRAFT_ROOT,
    DEFAULT_MARKDOWN_ROOT,
    DEFAULT_SPOKEN_MD_ROOT,
    DEFAULT_STANDALONE_VOICE_ROOT,
    DEFAULT_VIDEO_ROOT,
    DEFAULT_VOICE_ROOT,
    INTERNAL_WORKSPACE_ROOT,
)
from ..template_config import available_templates, image_set_for_template
from ..style_config import UIStyle
from ..sync_service import AUDIO_SUFFIXES, SyncService
from ..utils import compact_path, now_iso, safe_text, text_hash
from ..workflow_service import (
    DEFAULT_SUBTITLE_ASR_BEAM_SIZE,
    DEFAULT_SUBTITLE_ASR_MODEL,
    DEFAULT_SUBTITLE_ASR_WORKERS,
    VOICE_PROVIDER_INDEXTTS,
    VOICE_PROVIDER_MINIMAX,
    WorkflowRunResult,
    WorkflowService,
    account_voice_id_for_provider,
    markdown_file_to_voice_text,
    normalize_voice_provider,
    subtitle_entry_label,
    subtitle_manifest_entries,
    voice_provider_label,
)
from ..dialogs import TaskProgressDialog
from ..ui_helpers import (
    DialogSection,
    ProjectEditorState,
    VoiceTaskDraft,
    COLUMN_WIDTHS,
    TYPE_LABELS,
    _build_table,
    _set_tree_rows,
    _center_dialog,
    _restore_window,
    account_label_from_spoken_path,
    account_labels_for_voice_provider,
    asset_folder_paths,
    build_project_gap_details,
    build_project_issue_summary,
    collect_voice_status,
    confirm_project_markdown_path,
    configure_treeview_style,
    default_jianying_draft_name,
    default_spoken_markdown_path,
    entry_asset_issue_lines,
    entry_asset_lines,
    format_issue_preview,
    has_ready_asset,
    is_default_spoken_markdown_path,
    is_valid_windows_filename,
    manifest_account_label,
    manifest_display_template,
    manifest_entries,
    manifest_file_paths,
    manifest_missing_assets,
    manifest_product_video_gaps,
    normalized_name,
    open_path,
    parse_uid_list,
    parse_voice_targets,
    preview_lines,
    project_name_exists,
    project_selector_value,
    safe_file_component,
    selected_account_labels,
    show_action_sections_dialog,
    show_confirmation_dialog,
    show_precheck_dialog,
    show_text_dialog,
    split_missing_voice_rows_by_removed_assets,
    voice_block_display,
    voice_block_match_label,
    voice_block_uid,
    voice_generation_targets_from_rows,
    voice_inventory_stats,
    voice_row_choice_label,
    voice_state,
)

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..ui import App

from .workflow_page import WorkflowPage

class CutMePage(WorkflowPage):

    def __init__(self, master, app: App):
        super().__init__(master, app, "CutMe 引言")
        self.asset_folder_var = ctk.StringVar()
        self.intro_plan_var = ctk.StringVar()
        self.output_dir_var = ctk.StringVar()
        self.title_var = ctk.StringVar()

        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_LG))
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.pack(fill="x", padx=UIStyle.PAD_LG, pady=UIStyle.PAD_LG)
        inner.columnconfigure(1, weight=1)

        r = 0
        ctk.CTkLabel(inner, text="引言版本", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.intro_combo = AppComboBox(inner, variable=self.intro_choice_var)
        self.intro_combo.grid(row=r, column=1, sticky="ew", pady=UIStyle.PAD_XS)
        self.intro_combo.configure(command=lambda _=None: self._sync_intro_index())

        r += 1
        ctk.CTkLabel(inner, text="配音用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.account_input = AppComboBox(inner, width=200, variable=self.account_var)
        self.account_input.grid(row=r, column=1, sticky="ew", pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="画面标题", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(inner, textvariable=self.title_var).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="引言计划 JSON", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(inner, textvariable=self.intro_plan_var).grid(
            row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="选", width=50, command=self._browse_intro_plan).grid(
            row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="素材文件夹", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(inner, textvariable=self.asset_folder_var).grid(
            row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="选", width=50, command=self._browse_asset_folder).grid(
            row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="输出目录", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        AppEntry(inner, textvariable=self.output_dir_var).grid(
            row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="选", width=50, command=self._browse_output_dir).grid(
            row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(
            inner,
            text="选择引言计划 JSON 后会先做素材预检查和 ASR 场景对齐；不选择时继续使用旧 CutMe 素材文件夹流程。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=r, column=0, columnspan=3, sticky="w", pady=(UIStyle.PAD_XS, UIStyle.PAD_SM))

        act = ctk.CTkFrame(self.form_area, fg_color="transparent")
        act.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        PrimaryButton(act, text="    生成引言视频    ", command=self._run_cutme).pack(side="right")

    def refresh(self) -> None:
        self.app.sync_project_selectors()
        project = self.app.current_project()
        if project and self.loaded_project_id != project["id"]:
            self.loaded_project_id = project["id"]
            self._load_project_data(project)

    def _select_project(self, _=None) -> None:
        super()._select_project(_)
        project = self.app.current_project()
        if project:
            self._load_project_data(project)

    def _load_project_data(self, project: dict[str, Any]) -> None:
        self._refresh_intro_choices(project)

        users = [safe_text(a["label"]) for a in self.repo.accounts() if safe_text(a.get("label"))]
        self.account_input.configure(values=users)
        if users and self.account_var.get() not in users:
            self.account_var.set(users[0])

        category = safe_text(project.get("category_name")) or safe_text(project.get("name"))
        if category and not self.title_var.get():
            self.title_var.set(f"{category}怎么选？")

        if not self.output_dir_var.get():
            self.output_dir_var.set(str(CUTME_OUTPUT_ROOT))

    def _browse_asset_folder(self) -> None:
        initial = self.asset_folder_var.get().strip() or str(DEFAULT_IMAGE_ROOT)
        path = filedialog.askdirectory(initialdir=initial, title="选择引言素材文件夹")
        if path:
            self.asset_folder_var.set(path.replace("/", "\\"))

    def _browse_intro_plan(self) -> None:
        current = self.intro_plan_var.get().strip()
        initial = str(Path(current).parent) if current else str(INTERNAL_WORKSPACE_ROOT)
        path = filedialog.askopenfilename(
            initialdir=initial,
            title="选择引言计划 JSON",
            filetypes=(("JSON 文件", "*.json"), ("所有文件", "*.*")),
        )
        if path:
            self.intro_plan_var.set(path.replace("/", "\\"))

    def _browse_output_dir(self) -> None:
        initial = self.output_dir_var.get().strip() or str(CUTME_OUTPUT_ROOT)
        path = filedialog.askdirectory(initialdir=initial, title="选择输出目录")
        if path:
            self.output_dir_var.set(path.replace("/", "\\"))

    def _find_intro_voice(self, project: dict[str, Any], account_label: str, block_label: str) -> str | None:
        bindings = self.repo.asset_bindings(project["id"])
        for b in bindings:
            if (safe_text(b.get("uid")) == "INTRO"
                    and safe_text(b.get("asset_type")) == "voice"
                    and safe_text(b.get("account_label")) == account_label
                    and safe_text(b.get("status")) == "ready"
                    and safe_text(b.get("block_label")) == block_label):
                path = safe_text(b.get("path"))
                if path and Path(path).is_file():
                    return path
        for b in bindings:
            if (safe_text(b.get("uid")) == "INTRO"
                    and safe_text(b.get("asset_type")) == "voice"
                    and safe_text(b.get("account_label")) == account_label
                    and safe_text(b.get("status")) == "ready"):
                path = safe_text(b.get("path"))
                if path and Path(path).is_file():
                    return path
        return None

    def _get_intro_block(self, project: dict[str, Any]) -> dict[str, Any] | None:
        blocks = self.repo.script_blocks(project["id"])
        intro_blocks = [b for b in blocks if b["script_type"] == "intro"]
        if not intro_blocks:
            return None
        idx = max(0, int(self.intro_var.get() or "1") - 1)
        return intro_blocks[min(idx, len(intro_blocks) - 1)]

    def _run_cutme(self) -> None:
        project = self.project_required()
        if not project:
            return

        account_label = self.account_var.get().strip()
        if not account_label:
            messagebox.showwarning("缺少配音用户", "请选择一个配音用户。", parent=self)
            return

        block = self._get_intro_block(project)
        if not block:
            messagebox.showwarning("缺少引言文案", "当前项目没有引言文案，请先在文案中心同步。", parent=self)
            return

        block_label = safe_text(block.get("block_label")) or "引言"
        voice_path = self._find_intro_voice(project, account_label, block_label)
        if not voice_path:
            messagebox.showwarning(
                "缺少引言配音",
                f"找不到 [{account_label}] 的引言配音文件。\n请先在「生成配音」中为引言文案生成配音。",
                parent=self,
            )
            return

        asset_folder = self.asset_folder_var.get().strip()
        intro_plan_path = self.intro_plan_var.get().strip()
        title_text = self.title_var.get().strip() or safe_text(project.get("category_name")) or "精选推荐"
        output_dir = self.output_dir_var.get().strip() or str(CUTME_OUTPUT_ROOT)
        intro_text = safe_text(block.get("body")) or ""
        if not intro_plan_path:
            from ..cutme_intro import find_intro_plan_for_text

            matched_plan = find_intro_plan_for_text(int(project["id"]), intro_text)
            if matched_plan:
                intro_plan_path = str(matched_plan)
                self.intro_plan_var.set(str(matched_plan).replace("/", "\\"))

        category = safe_text(project.get("category_name")) or safe_text(project.get("name")) or "intro"
        output_filename = f"引言-{category}-{account_label}.mp4"
        output_path = Path(output_dir) / output_filename

        self.log(f"引言版本：{block_label}")
        self.log(f"配音文件：{voice_path}")
        self.log(f"引言计划：{intro_plan_path or '（未选择，使用旧流程）'}")
        self.log(f"素材文件夹：{asset_folder or '（无）'}")
        self.log(f"输出路径：{output_path}")
        self.log("开始生成引言视频...")

        def work() -> dict[str, Any]:
            if intro_plan_path:
                from ..cutme_intro import prepare_cutme_intro, run_cutme_render

                prepared = prepare_cutme_intro(
                    source_plan_path=intro_plan_path,
                    audio_path=voice_path,
                    project=project,
                    account_label=account_label,
                    script_block_id=int(block["id"]),
                    intro_text=intro_text,
                    title=title_text,
                    asset_folder=asset_folder,
                )
                result = run_cutme_render(prepared.config_path, output_path)
                return {
                    "path": result,
                    "prepared": prepared,
                }

            import sys as _sys
            _cutme_root = str(CUTME_ROOT)
            if _cutme_root not in _sys.path:
                _sys.path.insert(0, _cutme_root)
            import cutme_service

            audio_dur = cutme_service.get_audio_duration(voice_path)
            intro = cutme_service.IntroData(
                text=intro_text,
                audio_path=voice_path,
                audio_duration=audio_dur,
                title=title_text,
                subtitle="",
                params_points=[],
            )
            return cutme_service.generate_intro_video(
                intro,
                asset_folder or ".",
                output_path=output_path,
            )

        def on_success(payload: Any) -> None:
            result = payload["path"] if isinstance(payload, dict) else payload
            prepared = payload.get("prepared") if isinstance(payload, dict) else None
            size_mb = result.stat().st_size / 1024 / 1024
            if prepared:
                self.log(f"已准备 intro_plan：{prepared.intro_plan_path}")
                self.log(f"CutMe 配置：{prepared.config_path}")
                self.log(f"素材预检查：{'通过' if prepared.preflight.get('ok', True) else '未通过'}")
                self.log(f"ASR 场景对齐：{'已执行' if prepared.aligned_with_asr else '已使用现有 timing'}")
                self.log(f"已选素材：{json.dumps(prepared.selected_assets, ensure_ascii=False)}")
            self.log(f"生成完成：{result}")
            self.log(f"文件大小：{size_mb:.1f} MB")

        def on_error(exc: Exception, tb: str) -> None:
            self.log(f"生成失败：{exc}")
            if tb:
                self.log(tb)
            messagebox.showerror("生成失败", str(exc), parent=self)

        self.app.run_background(
            "CutMe 生成引言视频", work,
            on_success=on_success, on_error=on_error,
            show_success_toast=True,
        )
