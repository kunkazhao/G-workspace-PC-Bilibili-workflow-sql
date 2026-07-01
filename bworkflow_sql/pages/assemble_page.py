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

class AssemblePage(WorkflowPage):
    def __init__(self, master, app: App):
        self.asm_user_var = ctk.StringVar()
        self.template_var = ctk.StringVar()
        super().__init__(master, app, "组合口播稿")
        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        form.columnconfigure(5, weight=1)

        ctk.CTkLabel(form, text="口播用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        self.account_input = AppComboBox(form, variable=self.account_var)
        self.account_input.grid(row=0, column=1, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS))
        self.account_input.configure(command=self._on_asm_user_changed)

        ctk.CTkLabel(form, text="组合方式", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=2, sticky="w", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        mode_combo = AppComboBox(form, variable=self.mode_var, values=["标准模式", "Top 模式"], highlight_empty=False)
        mode_combo.grid(row=0, column=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS))
        mode_combo.configure(border_width=1, border_color=UIStyle.COLOR_PRIMARY)
        self.mode_var.set("标准模式")

        ctk.CTkLabel(form, text="引言", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=4, sticky="w", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        self.intro_combo = AppComboBox(form, variable=self.intro_choice_var, highlight_empty=False)
        self.intro_combo.grid(row=0, column=5, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS))
        self.intro_combo.configure(border_width=1, border_color=UIStyle.COLOR_PRIMARY)
        self.intro_combo.configure(command=lambda _=None: self._sync_intro_index())

        ctk.CTkLabel(form, text="Top 商品UID（可不填）", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        AppEntry(form, textvariable=self.uid_var).grid(row=1, column=1, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_XS)

        ctk.CTkLabel(form, text="展示模板", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=2, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        self.asm_template_combo = AppComboBox(form, variable=self.template_var)
        self.asm_template_combo.grid(row=1, column=3, columnspan=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_XS)

        ctk.CTkLabel(form, text="口播稿输出 MD", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=2, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_XS, 0)
        )
        self.spoken_md_entry = AppEntry(form, textvariable=self.spoken_md_var, highlight_empty=False)
        self.spoken_md_entry.grid(row=2, column=1, columnspan=4, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_XS, 0))
        self.spoken_md_entry.configure(border_width=1, border_color=UIStyle.COLOR_PRIMARY)
        GhostButton(form, text="选", width=52, command=self._browse_spoken_md).grid(row=2, column=5, sticky="e", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_XS, 0))
        ctk.CTkLabel(
            form,
            text="Top UID 用逗号分隔，支持中文和英文逗号；引言编号按 MD 中“引言文案”从上到下排序；展示模板会自动跟随口播用户。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=3, column=1, columnspan=5, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_XS, UIStyle.PAD_MD))

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=6, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        actions.columnconfigure(0, weight=1)
        PrimaryButton(actions, text="预检查并执行", command=self._run_command).grid(row=0, column=1, sticky="e")

    def _on_asm_user_changed(self, _=None, *, update_path: bool = True) -> None:
        from ..template_config import available_templates
        self.asm_user_var.set(self.account_var.get().strip())
        templates = available_templates(self.asm_user_var.get())
        self.asm_template_combo.configure(values=templates)
        if self.template_var.get() in templates:
            if update_path:
                self._set_default_spoken_md_if_needed(self.app.current_project())
            return
        if templates:
            self.template_var.set(templates[0])
        else:
            self.template_var.set("")
        if update_path:
            self._set_default_spoken_md_if_needed(self.app.current_project())

    def _display_template_for_account(self) -> str:
        self._on_asm_user_changed(update_path=False)
        return self.template_var.get().strip()
