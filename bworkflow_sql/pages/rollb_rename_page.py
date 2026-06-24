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

class RollBRenamePage(WorkflowPage):
    def __init__(self, master, app: App):
        super().__init__(master, app, "roll-b改名")
        self.directory_var = ctk.StringVar()

        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_LG))
        form.columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="视频目录", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        AppEntry(form, textvariable=self.directory_var).grid(
            row=0, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )
        GhostButton(form, text="选择目录", width=92, command=self._browse_video_dir).grid(
            row=0, column=2, sticky="e", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_XS)
        )

        ctk.CTkLabel(
            form,
            text="目录下的视频文件名需要包含商品 UID；预检查会生成改名计划，确认后才会实际改名并同步视频素材。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_MD))

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=2, column=0, columnspan=3, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        actions.columnconfigure(0, weight=1)
        PrimaryButton(actions, text="预检查并执行", command=self._run_roll_b_rename).grid(row=0, column=1, sticky="e")

    def refresh(self) -> None:
        previous_project_id = self.loaded_project_id
        super().refresh()
        project = self.app.current_project()
        current = self.directory_var.get().strip()
        if project and (previous_project_id != project["id"] or not current or current == str(DEFAULT_VIDEO_ROOT)):
            self.directory_var.set(str(Path(safe_text(project.get("video_root")) or DEFAULT_VIDEO_ROOT) / project_category_folder(project)))

    def _browse_video_dir(self) -> None:
        project = self.app.current_project()
        current = self.directory_var.get().strip()
        initial = current
        if not initial and project:
            initial = str(Path(safe_text(project.get("video_root")) or DEFAULT_VIDEO_ROOT) / project_category_folder(project))
        path = filedialog.askdirectory(initialdir=initial or str(DEFAULT_VIDEO_ROOT), title="选择 roll-b 视频目录")
        if path:
            self.directory_var.set(path.replace("/", "\\"))

    def _roll_b_precheck_sections(self, project: dict[str, Any], preview: dict[str, Any]) -> list[DialogSection]:
        counts = preview.get("counts") or {}
        items = [item for item in preview.get("items", []) if isinstance(item, dict)]
        rename_items = [item for item in items if safe_text(item.get("status")) == "rename"]
        skipped_items = [item for item in items if safe_text(item.get("status")) == "skipped"]
        blocked_items = [item for item in items if safe_text(item.get("status")) == "blocked"]
        blockers = [safe_text(item) for item in preview.get("blockers") or [] if safe_text(item)]
        rename_lines = [
            f"{safe_text(item.get('source_name'))} → {safe_text(item.get('target_name'))}"
            for item in rename_items
        ]
        skipped_lines = [
            f"{safe_text(item.get('source_name'))}：{safe_text(item.get('message'))}"
            for item in skipped_items
        ]
        blocked_lines = blockers + [
            f"{safe_text(item.get('source_name'))}：{safe_text(item.get('message'))}"
            for item in blocked_items
        ]
        return [
            DialogSection(
                title="项目信息",
                step="1",
                tone="primary",
                rows=[
                    ("项目", safe_text(project.get("name"))),
                    ("视频目录", safe_text(preview.get("directory")) or self.directory_var.get().strip() or "未选择"),
                    ("命名格式", "价格元-UID-商品名称；多个视频自动加 -1 / -2"),
                ],
            ),
            DialogSection(
                title="改名统计",
                step="2",
                tone="success" if preview.get("can_execute") else "warning",
                rows=[
                    ("待改名", f"{counts.get('rename', 0)} 个"),
                    ("已是目标格式", f"{counts.get('unchanged', 0)} 个"),
                    ("跳过", f"{counts.get('skipped', 0)} 个"),
                    ("阻塞", f"{counts.get('blocked', 0) + len(blockers)} 个"),
                ],
                helper="确认后只重命名预览中的待改名视频，不删除文件，不覆盖已有目标文件。",
            ),
            DialogSection(
                title="改名计划",
                step="3",
                tone="info",
                items=preview_lines(rename_lines or ["当前没有需要改名的视频。"], limit=120),
            ),
            DialogSection(
                title="跳过和阻塞",
                step="4",
                tone="warning" if blocked_lines else "success",
                items=preview_lines(blocked_lines + skipped_lines, limit=120) if (blocked_lines or skipped_lines) else [],
                helper="" if (blocked_lines or skipped_lines) else "当前没有阻塞项；已是目标格式的文件不再展开。",
            ),
        ]

    def _run_roll_b_rename(self) -> None:
        project = self.project_required()
        if not project:
            return
        directory = self.directory_var.get().strip()
        try:
            preview = self.workflow.preview_roll_b_rename(project["id"], directory)
        except Exception as exc:
            messagebox.showerror("预检查失败", str(exc))
            return
        sections = self._roll_b_precheck_sections(project, preview)
        if not show_precheck_dialog(
            self,
            "roll-b改名预检查",
            "请核对本次视频改名计划，确认无误后再执行。",
            sections,
            can_continue=bool(preview.get("can_execute")),
            confirm_text="确认改名",
        ):
            return

        progress_dialog = TaskProgressDialog(self, "正在执行 roll-b 改名", "正在重命名视频并同步素材状态...")
        progress_dialog.append(f"项目：{safe_text(project.get('name'))}")
        progress_dialog.append(f"视频目录：{directory}")
        progress_dialog.append("")

        def work() -> dict[str, Any]:
            result = self.workflow.execute_roll_b_rename(project["id"], directory)
            sync_result = self.sync.sync_assets(project["id"], asset_type="video", root_override=directory)
            return {"rename": result, "sync": sync_result}

        def on_success(payload: dict[str, Any]) -> None:
            rename_result = payload.get("rename") or {}
            sync_result = payload.get("sync") or {}
            renamed_items = rename_result.get("renamed_items") or []
            progress_dialog.append(f"[成功] {safe_text(rename_result.get('result_message')) or 'roll-b 改名完成。'}")
            for item in renamed_items[:80]:
                progress_dialog.append(f"[改名] {Path(safe_text(item.get('source_path'))).name} → {safe_text(item.get('target_name'))}")
            if len(renamed_items) > 80:
                progress_dialog.append(f"[提示] 另有 {len(renamed_items) - 80} 个视频已改名，日志中省略。")
            progress_dialog.append(
                f"[同步] 视频素材同步完成：命中 {sync_result.get('video', 0)} 个，缺素材 {sync_result.get('unmatched', 0)} 个。"
            )
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"roll-b改名完成：{rename_result.get('renamed', 0)} 个\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
            progress_dialog.finish(
                "roll-b 改名已执行完成。",
                kind="success",
                headline="roll-b改名完成",
                detail=f"已改名 {rename_result.get('renamed', 0)} 个视频",
            )
            self.app.toast("roll-b改名完成")

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"执行失败：{exc}", kind="error")
            messagebox.showerror("执行失败", str(exc))

        self.app.run_background("roll-b改名", work, on_success=on_success, on_error=on_error, show_success_toast=False)
