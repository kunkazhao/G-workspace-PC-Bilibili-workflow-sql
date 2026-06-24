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


class AccountPage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "用户管理", app)
        self.vars = {
            key: ctk.StringVar()
            for key in [
                "label",
                "account_id",
                "voice_id",
                "minimax_voice_id",
                "voice_name",
                "media_identity",
                "closing_audio_path",
            ]
        }

        card = AppCard(self.content, "新增/更新用户")
        f = ctk.CTkFrame(card, fg_color="transparent")
        f.columnconfigure(1, weight=1)
        f.columnconfigure(3, weight=1)
        labels = [
            ("用户名称", "label"),
            ("账号标识", "account_id"),
            ("IndexTTS 音色标识", "voice_id"),
            ("MiniMax 音色标识", "minimax_voice_id"),
            ("音色名称", "voice_name"),
            ("素材身份", "media_identity"),
            ("结尾配音路径", "closing_audio_path"),
        ]
        for idx, (label, key) in enumerate(labels):
            r = idx // 2
            c = (idx % 2) * 2
            ctk.CTkLabel(f, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=r, column=c, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
            AppEntry(f, textvariable=self.vars[key]).grid(row=r, column=c + 1, sticky="ew", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_XS)
        PrimaryButton(f, text="保存用户", command=self._save_account).grid(row=4, column=0, sticky="w", pady=UIStyle.PAD_SM)
        GhostButton(f, text="导入旧项目用户/音色", command=self._import_legacy).grid(row=4, column=1, sticky="w", pady=UIStyle.PAD_SM)
        ctk.CTkLabel(f, text="说明：页面仍按同一个用户名称选择；IndexTTS 和 MiniMax 分别使用各自的音色标识。",
                     font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM).grid(row=5, column=0, columnspan=4, sticky="w", pady=UIStyle.PAD_SM)
        card.add_content(f)

        outer = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        outer.pack(fill="both", expand=True)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)
        self.tree = _build_table(outer, ("用户名称", "账号标识", "IndexTTS 音色", "MiniMax 音色", "音色名称", "素材身份", "结尾配音", "启用"), row=0)

    def _save_account(self) -> None:
        payload = {k: v.get().strip() for k, v in self.vars.items()}
        if not payload["label"]:
            self.toast("请填写用户标签，例如小燃。", kind="warning")
            return
        ts = now_iso()
        with self.db.connect() as conn:
            conn.execute("""
                INSERT INTO accounts (label, account_id, voice_id, minimax_voice_id, voice_name, media_identity, closing_audio_path, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(label) DO UPDATE SET
                    account_id=excluded.account_id, voice_id=excluded.voice_id, minimax_voice_id=excluded.minimax_voice_id, voice_name=excluded.voice_name,
                    media_identity=excluded.media_identity, closing_audio_path=excluded.closing_audio_path, updated_at=excluded.updated_at
            """, (payload["label"], payload["account_id"], payload["voice_id"], payload["minimax_voice_id"], payload["voice_name"], payload["media_identity"], payload["closing_audio_path"], ts, ts))
        self.refresh()
        self.toast("用户已保存")

    def _import_legacy(self) -> None:
        self.app.run_background("导入旧项目用户/音色",
                                lambda: (self.legacy_import.import_accounts(), self.legacy_import.import_voice_profiles()),
                                on_success=lambda r: (self.toast("导入完成"), self.refresh()), show_success_toast=False)

    def refresh(self) -> None:
        rows = [
            (
                a["label"],
                a["account_id"],
                a["voice_id"],
                a.get("minimax_voice_id", ""),
                a["voice_name"],
                a["media_identity"],
                compact_path(a["closing_audio_path"], 40),
                "是" if a["enabled"] else "否",
            )
            for a in self.repo.accounts()
        ]
        _set_tree_rows(self.tree, rows)
