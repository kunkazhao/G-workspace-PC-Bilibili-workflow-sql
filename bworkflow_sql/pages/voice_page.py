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

class VoicePage(WorkflowPage):
    def __init__(self, master, app: App):
        super().__init__(master, app, "生成配音")
        self.extra_voice_tasks: list[VoiceTaskDraft] = []
        self.voice_provider_var = ctk.StringVar(value="IndexTTS 本地服务")
        self.voice_output_dir_var = ctk.StringVar(value="请先选择项目和配音用户")
        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        form.columnconfigure(1, weight=0)
        form.columnconfigure(3, weight=1)

        ctk.CTkLabel(form, text="配音用户", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        self.account_input = AppComboBox(form, width=180, variable=self.account_var)
        self.account_input.grid(row=0, column=1, sticky="w", pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))
        self.account_input.configure(command=self._on_voice_account_changed)

        ctk.CTkLabel(form, text="配音方式", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=2, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM)
        )
        self.provider_segment = ctk.CTkSegmentedButton(
            form,
            values=["IndexTTS 本地服务", "MiniMax API"],
            variable=self.voice_provider_var,
            command=lambda _=None: self._on_voice_provider_changed(),
        )
        self.provider_segment.grid(row=0, column=3, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))

        ctk.CTkLabel(form, text="商品UID / 文案ID（可不填）", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(0, UIStyle.PAD_SM)
        )
        AppEntry(form, textvariable=self.uid_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_SM)
        )

        ctk.CTkLabel(
            form,
            text="留空处理全部文案；填商品 UID 会处理该商品全部版本；填 script_id 只处理指定文案版本，多个值用逗号分隔。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=2, column=1, columnspan=3, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_MD))

        ctk.CTkLabel(form, text="配音保存目录", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=3, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=(0, UIStyle.PAD_MD)
        )
        output_entry = AppEntry(form, textvariable=self.voice_output_dir_var)
        output_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=(0, UIStyle.PAD_MD))
        GhostButton(form, text="选择目录", command=self._browse_voice_output_dir, width=92).grid(
            row=3, column=3, sticky="e", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_MD)
        )

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=4, sticky="ew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        actions.columnconfigure(0, weight=1)
        GhostButton(actions, text="添加任务", command=self._open_add_voice_task_dialog).grid(row=0, column=1, sticky="e", padx=(0, UIStyle.PAD_SM))
        PrimaryButton(actions, text="预检查并执行", command=self._run_command).grid(row=0, column=2, sticky="e")

        self.voice_task_list = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        self.voice_task_list.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        self._render_voice_task_list()
        self.account_var.trace_add("write", lambda *_: self._update_voice_output_dir(force=True))
        self._update_voice_output_dir()

    def _on_voice_account_changed(self, _=None) -> None:
        self._update_voice_output_dir(force=True)

    def _on_voice_provider_changed(self) -> None:
        self.refresh()
        self._update_voice_output_dir(force=True)

    def _selected_voice_provider(self) -> str:
        return normalize_voice_provider(self.voice_provider_var.get())

    def _update_voice_output_dir(self, *, force: bool = False) -> None:
        project = self.app.current_project()
        account_label = self.account_var.get().strip()
        if not project:
            self.voice_output_dir_var.set("请先选择品类项目")
            return
        if not account_label:
            self.voice_output_dir_var.set("请先选择配音用户")
            return
        try:
            output_dir = self.workflow.expected_voice_output_dir(project["id"], account_label=account_label)
        except Exception as exc:
            self.voice_output_dir_var.set(f"无法计算保存目录：{exc}")
            return
        current = self.voice_output_dir_var.get().strip()
        if current and not force and not current.startswith("请先") and not current.startswith("无法"):
            return
        self.voice_output_dir_var.set(str(output_dir))

    def _browse_voice_output_dir(self) -> None:
        project = self.app.current_project()
        initial = self.voice_output_dir_var.get().strip()
        if initial.startswith("请先") or initial.startswith("无法"):
            initial = safe_text(project.get("voice_root")) if project else str(DEFAULT_VOICE_ROOT)
        path = filedialog.askdirectory(initialdir=initial or str(DEFAULT_VOICE_ROOT), title="选择配音保存目录")
        if path:
            self.voice_output_dir_var.set(path.replace("/", "\\"))

    def _current_voice_task(self) -> VoiceTaskDraft | None:
        project = self.app.current_project()
        account_label = self.account_var.get().strip()
        output_dir = self.voice_output_dir_var.get().strip()
        if not project or not account_label:
            return None
        if not output_dir or output_dir.startswith("请先") or output_dir.startswith("无法"):
            return None
        return VoiceTaskDraft(
            project_id=int(project["id"]),
            project_name=safe_text(project.get("name")),
            account_label=account_label,
            target_text=self.uid_var.get().strip(),
            output_dir=output_dir,
            voice_provider=self._selected_voice_provider(),
        )

    def _voice_tasks(self) -> list[VoiceTaskDraft]:
        tasks: list[VoiceTaskDraft] = []
        current = self._current_voice_task()
        if current:
            tasks.append(current)
        tasks.extend(self.extra_voice_tasks)
        return tasks

    def _project_from_selector_value(self, value: str) -> dict[str, Any] | None:
        project_id = self.app.project_id_for_selector_value(value)
        if project_id is None:
            return None
        return self.repo.project(project_id)

    def _open_add_voice_task_dialog(self) -> None:
        projects = self.repo.projects()
        provider_var = ctk.StringVar(value=self.voice_provider_var.get())
        users = account_labels_for_voice_provider(self.repo.accounts(), normalize_voice_provider(provider_var.get()))
        if not projects:
            self.toast("请先在“品类项目”中创建或选择项目。", kind="warning")
            return
        if not users:
            self.toast("请先在“用户管理”中配置配音用户。", kind="warning")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("添加配音任务")
        dialog.geometry("760x360")
        dialog.minsize(680, 320)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)

        project_values = [project_selector_value(p) for p in projects]
        current_project = self.app.current_project()
        project_var = ctk.StringVar(value=project_selector_value(current_project) if current_project else project_values[0])
        account_var = ctk.StringVar(value=self.account_var.get().strip() if self.account_var.get().strip() in users else users[0])
        target_var = ctk.StringVar()
        output_var = ctk.StringVar(value="")

        def available_users() -> list[str]:
            return account_labels_for_voice_provider(self.repo.accounts(), normalize_voice_provider(provider_var.get()))

        def refresh_users(_=None) -> None:
            values = available_users()
            account_combo.configure(values=values)
            if values and account_var.get() not in values:
                account_var.set(values[0])
            elif not values:
                account_var.set("")
            update_output()

        def update_output(_=None) -> None:
            project = self._project_from_selector_value(project_var.get())
            account_label = account_var.get().strip()
            if not project or not account_label:
                output_var.set("请选择品类项目和配音用户")
                return
            try:
                output_var.set(str(self.workflow.expected_voice_output_dir(project["id"], account_label=account_label)))
            except Exception as exc:
                output_var.set(f"无法计算保存目录：{exc}")

        def add_row(row: int, label: str, widget: tk.Widget, *, columnspan: int = 1) -> None:
            ctk.CTkLabel(dialog, text=label, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
                row=row, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=UIStyle.PAD_SM
            )
            widget.grid(row=row, column=1, columnspan=columnspan, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_SM)

        project_combo = AppComboBox(dialog, values=project_values, variable=project_var)
        project_combo.configure(command=update_output)
        add_row(0, "品类项目", project_combo)
        account_combo = AppComboBox(dialog, values=users, variable=account_var)
        account_combo.configure(command=update_output)
        add_row(1, "配音用户", account_combo)
        provider_segment = ctk.CTkSegmentedButton(
            dialog,
            values=["IndexTTS 本地服务", "MiniMax API"],
            variable=provider_var,
            command=refresh_users,
        )
        add_row(2, "配音方式", provider_segment)
        add_row(3, "商品UID / 文案ID（可不填）", AppEntry(dialog, textvariable=target_var))
        add_row(4, "配音保存目录", AppEntry(dialog, textvariable=output_var), columnspan=1)

        def browse_output_dir() -> None:
            project = self._project_from_selector_value(project_var.get())
            initial = output_var.get().strip()
            if initial.startswith("请选择") or initial.startswith("无法"):
                initial = safe_text(project.get("voice_root")) if project else str(DEFAULT_VOICE_ROOT)
            path = filedialog.askdirectory(initialdir=initial or str(DEFAULT_VOICE_ROOT), title="选择配音保存目录")
            if path:
                output_var.set(path.replace("/", "\\"))

        GhostButton(dialog, text="选择目录", command=browse_output_dir, width=92).grid(
            row=4, column=2, sticky="e", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_SM
        )

        ctk.CTkLabel(
            dialog,
            text="留空处理全部文案；多个 UID 或 script_id 用逗号分隔。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
            anchor="w",
        ).grid(row=5, column=1, sticky="w", padx=(0, UIStyle.PAD_LG), pady=(0, UIStyle.PAD_MD))

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_SM, UIStyle.PAD_LG))
        buttons.columnconfigure(0, weight=1)

        def confirm() -> None:
            project = self._project_from_selector_value(project_var.get())
            account_label = account_var.get().strip()
            output_dir = output_var.get().strip()
            if not project or not account_label or not output_dir or output_dir.startswith("无法"):
                messagebox.showwarning("无法添加", "请填写有效的品类项目、配音用户和配音保存目录。")
                return
            self.extra_voice_tasks.append(
                VoiceTaskDraft(
                    project_id=int(project["id"]),
                    project_name=safe_text(project.get("name")),
                    account_label=account_label,
                    target_text=target_var.get().strip(),
                    output_dir=output_dir,
                    voice_provider=normalize_voice_provider(provider_var.get()),
                )
            )
            dialog.destroy()
            self._render_voice_task_list()

        GhostButton(buttons, text="取消", command=dialog.destroy).grid(row=0, column=1, padx=(0, UIStyle.PAD_SM))
        PrimaryButton(buttons, text="添加", command=confirm).grid(row=0, column=2)
        update_output()
        _center_dialog(dialog)
        dialog.wait_window()

    def _render_voice_task_list(self) -> None:
        for child in self.voice_task_list.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.voice_task_list,
            text="已添加的额外配音任务",
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            anchor="w",
        ).pack(anchor="w", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))
        if not self.extra_voice_tasks:
            ctk.CTkLabel(
                self.voice_task_list,
                text="当前没有额外任务。点击“添加任务”后，会在这里显示并参与预检查与执行。",
                font=UIStyle.FONT_SMALL,
                text_color=UIStyle.COLOR_TEXT_DIM,
                anchor="w",
            ).pack(fill="x", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
            return
        for index, task in enumerate(self.extra_voice_tasks, start=1):
            row = ctk.CTkFrame(self.voice_task_list, fg_color="transparent")
            row.pack(fill="x", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))
            summary = f"{index}. {task.project_name}｜{task.account_label}｜{voice_provider_label(task.voice_provider)}｜{task.display_target}"
            ctk.CTkLabel(row, text=summary, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_MAIN, anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(row, text=compact_path(task.output_dir, 42), font=UIStyle.FONT_SMALL, text_color=UIStyle.COLOR_TEXT_DIM, anchor="e").pack(side="left", padx=(UIStyle.PAD_SM, UIStyle.PAD_SM))
            GhostButton(row, text="删除", width=72, height=34, command=lambda i=index - 1: self._delete_voice_task(i)).pack(side="left")
        ctk.CTkLabel(self.voice_task_list, text="", height=1).pack(pady=(0, UIStyle.PAD_SM))

    def _delete_voice_task(self, index: int) -> None:
        if 0 <= index < len(self.extra_voice_tasks):
            del self.extra_voice_tasks[index]
            self._render_voice_task_list()

    def _voice_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool]:
        tasks = self._voice_tasks()
        if not tasks:
            return [
                DialogSection(
                    title="没有可执行任务",
                    step="1",
                    tone="warning",
                    items=["请先填写当前表单，或点击“添加任务”新增一个配音任务。"],
                )
            ], False
        sections: list[DialogSection] = []
        totals = {"pending": 0, "skipped": 0, "blocked": 0}
        can_continue = False
        task_sections: list[DialogSection] = []
        for index, task in enumerate(tasks, start=1):
            task_project = self.repo.project(task.project_id)
            if not task_project:
                totals["blocked"] += 1
                task_sections.append(
                    DialogSection(
                        title=f"任务 {index}｜阻塞与缺口",
                        step=str(2 + (index - 1) * 5),
                        tone="warning",
                        items=[f"{task.project_name or task.project_id}：品类项目不存在"],
                    )
                )
                continue
            current_sections, stats, current_can_continue = self._voice_task_precheck_sections(
                task_project,
                account_label=task.account_label,
                voice_provider=task.voice_provider,
                target_text=task.target_text,
                output_dir_text=task.output_dir,
                task_title=f"任务 {index}",
                step_start=2 + (index - 1) * 4,
            )
            for key in totals:
                totals[key] += stats[key]
            can_continue = can_continue or current_can_continue
            task_sections.extend(current_sections)
        sections.append(
            DialogSection(
                title="本次配音任务总览",
                step="1",
                tone="primary",
                rows=[
                    ("任务数", f"{len(tasks)} 个"),
                    ("待生成 / 重生成", f"{totals['pending']} 条"),
                    ("已有配音跳过", f"{totals['skipped']} 条"),
                    ("缺文案 / 不可处理", f"{totals['blocked']} 条"),
                ],
                helper="确认后会按任务顺序执行；已有配音继续跳过，缺失和过期配音会重新生成。",
            )
        )
        sections.extend(task_sections)
        return sections, can_continue
