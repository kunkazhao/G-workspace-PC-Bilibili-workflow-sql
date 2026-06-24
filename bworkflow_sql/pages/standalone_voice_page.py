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


class StandaloneVoicePage(BasePage):
    def __init__(self, master, app: App):
        super().__init__(master, "单独配音", app)
        self.input_mode_var = ctk.StringVar(value="粘贴文字")
        self.voice_provider_var = ctk.StringVar(value="IndexTTS 本地服务")
        self.voice_mode_var = ctk.StringVar(value="已配置用户音色")
        self.account_var = ctk.StringVar()
        self.md_path_var = ctk.StringVar()
        self.reference_audio_var = ctk.StringVar()
        self.output_dir_var = ctk.StringVar(value=str(DEFAULT_STANDALONE_VOICE_ROOT))
        self.text_placeholder = "粘贴文字文案在这里"
        self.text_placeholder_visible = False

        input_card = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        input_card.pack(fill="both", expand=True, pady=(0, UIStyle.PAD_SM))
        input_card.grid_columnconfigure(0, weight=1)
        input_card.grid_rowconfigure(2, weight=1)

        input_header = ctk.CTkFrame(input_card, fg_color="transparent")
        input_header.grid(row=0, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))
        ctk.CTkLabel(input_header, text="输入内容", font=UIStyle.FONT_H2, text_color=UIStyle.COLOR_TEXT_MAIN).pack(side="left")
        ctk.CTkLabel(input_header, text="输入方式", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(UIStyle.PAD_XL, UIStyle.PAD_SM))
        self.input_segment = ctk.CTkSegmentedButton(
            input_header,
            values=["粘贴文字", "选择文档"],
            variable=self.input_mode_var,
            command=lambda _=None: self._sync_input_mode(),
        )
        self.input_segment.pack(side="left")

        md_row = ctk.CTkFrame(input_card, fg_color="transparent")
        md_row.grid(row=1, column=0, sticky="w", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(md_row, text="MD 文档", width=74, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM, anchor="w").pack(side="left", padx=(0, UIStyle.PAD_SM))
        self.md_entry = AppEntry(md_row, textvariable=self.md_path_var, width=560)
        self.md_entry.pack(side="left", padx=(0, UIStyle.PAD_SM))
        self.md_button = GhostButton(md_row, text="选择 MD", command=self._browse_md, width=92)
        self.md_button.pack(side="left")

        self.text_input = AppTextbox(input_card, height=300, wrap="word")
        self.text_input.grid(row=2, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        self.text_input.bind("<FocusIn>", lambda _event: self._clear_text_placeholder())
        self.text_input.bind("<FocusOut>", lambda _event: self._restore_text_placeholder())

        voice_card = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        voice_card.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        voice_card.grid_columnconfigure(0, weight=1)

        voice_header = ctk.CTkFrame(voice_card, fg_color="transparent")
        voice_header.grid(row=0, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_SM))
        ctk.CTkLabel(voice_header, text="音色与输出", font=UIStyle.FONT_H2, text_color=UIStyle.COLOR_TEXT_MAIN).pack(side="left")
        ctk.CTkLabel(voice_header, text="配音方式", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(UIStyle.PAD_XL, UIStyle.PAD_SM))
        self.provider_segment = ctk.CTkSegmentedButton(
            voice_header,
            values=["IndexTTS 本地服务", "MiniMax API"],
            variable=self.voice_provider_var,
            command=lambda _=None: self._sync_voice_provider(),
        )
        self.provider_segment.pack(side="left")
        ctk.CTkLabel(voice_header, text="音色来源", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).pack(side="left", padx=(UIStyle.PAD_XL, UIStyle.PAD_SM))
        self.voice_segment = ctk.CTkSegmentedButton(
            voice_header,
            values=["已配置用户音色", "参考音频文件"],
            variable=self.voice_mode_var,
            command=lambda _=None: self._sync_voice_mode(),
        )
        self.voice_segment.pack(side="left")

        voice_fields = ctk.CTkFrame(voice_card, fg_color="transparent")
        voice_fields.grid(row=1, column=0, sticky="w", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_LG))
        voice_fields.grid_columnconfigure(1, minsize=260)

        ctk.CTkLabel(voice_fields, text="配音用户", width=74, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM, anchor="w").grid(row=0, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.account_combo = AppComboBox(voice_fields, width=220, variable=self.account_var)
        self.account_combo.grid(row=0, column=1, sticky="w", pady=UIStyle.PAD_XS)

        ctk.CTkLabel(voice_fields, text="参考音频", width=74, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM, anchor="w").grid(row=1, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.reference_entry = AppEntry(voice_fields, textvariable=self.reference_audio_var, width=560)
        self.reference_entry.grid(row=1, column=1, sticky="w", pady=UIStyle.PAD_XS)
        self.reference_button = GhostButton(voice_fields, text="上传", command=self._browse_reference_audio, width=72)
        self.reference_button.grid(row=1, column=2, sticky="w", padx=(UIStyle.PAD_SM, 0), pady=UIStyle.PAD_XS)

        ctk.CTkLabel(voice_fields, text="输出目录", width=74, font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM, anchor="w").grid(row=2, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        self.output_entry = AppEntry(voice_fields, textvariable=self.output_dir_var, width=560)
        self.output_entry.grid(row=2, column=1, sticky="w", pady=UIStyle.PAD_XS)
        GhostButton(voice_fields, text="选择目录", command=self._browse_output_dir, width=92).grid(row=2, column=2, sticky="w", padx=(UIStyle.PAD_SM, 0), pady=UIStyle.PAD_XS)

        actions = ctk.CTkFrame(self.content, fg_color="transparent")
        actions.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        actions.columnconfigure(0, weight=1)
        PrimaryButton(actions, text="预检查并生成", command=self._run_standalone_voice).grid(row=0, column=1, sticky="e")

        self.log_text = AppTextbox(self.content, height=160)
        self.log_text.pack(fill="both", expand=True)
        self._refresh_accounts()
        self._show_text_placeholder()
        self._sync_input_mode()
        self._sync_voice_provider()
        self._sync_voice_mode()

    def refresh(self) -> None:
        self._refresh_accounts()

    def _show_text_placeholder(self) -> None:
        self.text_input.configure(state="normal", text_color=UIStyle.COLOR_TEXT_DIM)
        self.text_input.delete("1.0", "end")
        self.text_input.insert("1.0", self.text_placeholder)
        self.text_placeholder_visible = True

    def _clear_text_placeholder(self) -> None:
        if not self.text_placeholder_visible:
            return
        self.text_input.configure(state="normal", text_color=UIStyle.COLOR_TEXT_MAIN)
        self.text_input.delete("1.0", "end")
        self.text_placeholder_visible = False

    def _restore_text_placeholder(self) -> None:
        if self.input_mode_var.get() != "粘贴文字":
            return
        if self.text_input.get("1.0", "end").strip():
            return
        self._show_text_placeholder()

    def _refresh_accounts(self) -> None:
        provider = self._selected_voice_provider()
        labels = account_labels_for_voice_provider(self.repo.accounts(), provider)
        self.account_combo.configure(values=labels)
        if labels and self.account_var.get() not in labels:
            self.account_var.set(labels[0])
        if not labels:
            self.account_var.set("")
        if provider == VOICE_PROVIDER_INDEXTTS and not labels and self.voice_mode_var.get() == "已配置用户音色":
            self.voice_mode_var.set("参考音频文件")

    def _sync_input_mode(self) -> None:
        md_enabled = self.input_mode_var.get() == "选择文档"
        self.md_entry.configure(state="normal" if md_enabled else "disabled")
        self.md_button.configure(state="normal" if md_enabled else "disabled")
        if md_enabled:
            self.text_input.configure(state="disabled", text_color=UIStyle.COLOR_TEXT_DIM)
            return
        self.text_input.configure(state="normal", text_color=UIStyle.COLOR_TEXT_DIM if self.text_placeholder_visible else UIStyle.COLOR_TEXT_MAIN)
        self._restore_text_placeholder()

    def _selected_voice_provider(self) -> str:
        return normalize_voice_provider(self.voice_provider_var.get())

    def _sync_voice_provider(self) -> None:
        if self._selected_voice_provider() == VOICE_PROVIDER_MINIMAX:
            self.voice_mode_var.set("已配置用户音色")
        self._sync_voice_mode()

    def _sync_voice_mode(self) -> None:
        provider = self._selected_voice_provider()
        if provider == VOICE_PROVIDER_MINIMAX:
            self.account_combo.configure(state="normal")
            self.reference_entry.configure(state="disabled")
            self.reference_button.configure(state="disabled")
            self.reference_audio_var.set("")
            if not self.account_var.get().strip():
                labels = account_labels_for_voice_provider(self.repo.accounts(), provider)
                if labels:
                    self.account_var.set(labels[0])
            return
        user_mode = self.voice_mode_var.get() == "已配置用户音色"
        self.account_combo.configure(state="normal" if user_mode else "disabled")
        self.reference_entry.configure(state="disabled" if user_mode else "normal")
        self.reference_button.configure(state="disabled" if user_mode else "normal")
        if user_mode:
            self.reference_audio_var.set("")
            if not self.account_var.get().strip():
                labels = account_labels_for_voice_provider(self.repo.accounts(), VOICE_PROVIDER_INDEXTTS)
                if labels:
                    self.account_var.set(labels[0])
        else:
            self.account_var.set("")

    def _browse_md(self) -> None:
        path = filedialog.askopenfilename(title="选择 MD 文档", initialdir=str(DEFAULT_MARKDOWN_ROOT), filetypes=[("Markdown", "*.md")])
        if not path:
            return
        if Path(path).suffix.casefold() != ".md":
            self.toast("单独配音只支持选择 .md 文档。", kind="warning")
            return
        self.md_path_var.set(path.replace("/", "\\"))

    def _browse_reference_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="上传参考音频",
            initialdir=str(DEFAULT_STANDALONE_VOICE_ROOT),
            filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma")],
        )
        if path:
            self.reference_audio_var.set(path.replace("/", "\\"))

    def _browse_output_dir(self) -> None:
        initial = self.output_dir_var.get().strip() or str(DEFAULT_STANDALONE_VOICE_ROOT)
        path = filedialog.askdirectory(initialdir=initial, title="选择配音输出目录")
        if path:
            self.output_dir_var.set(path.replace("/", "\\"))

    def _input_text_and_label(self) -> tuple[str, str, str]:
        if self.input_mode_var.get() == "选择文档":
            path_text = self.md_path_var.get().strip()
            text = markdown_file_to_voice_text(path_text)
            return text, "MD 文档", Path(path_text).stem
        text = "" if self.text_placeholder_visible else self.text_input.get("1.0", "end").strip()
        return text, "粘贴文字", "粘贴文本"

    def _voice_source(self) -> tuple[str, str, str]:
        if self._selected_voice_provider() == VOICE_PROVIDER_MINIMAX:
            account_label = self.account_var.get().strip()
            if not account_label:
                raise ValueError("MiniMax API 配音需要选择一个已配置用户音色。")
            return "MiniMax 用户音色", account_label, ""
        if self.voice_mode_var.get() == "已配置用户音色":
            account_label = self.account_var.get().strip()
            if not account_label:
                raise ValueError("请选择一个已配置用户音色。")
            return "用户音色", account_label, ""
        reference = self.reference_audio_var.get().strip()
        if not reference:
            raise ValueError("请上传参考音频文件。")
        return "参考音频", "", reference

    def _precheck_sections(self) -> tuple[list[DialogSection], bool, dict[str, str]]:
        blocked: list[str] = []
        payload = {"text": "", "source_label": "", "account_label": "", "reference_audio_path": ""}
        try:
            text, input_label, source_label = self._input_text_and_label()
            payload["text"] = text
            payload["source_label"] = source_label
            if not text:
                blocked.append("输入内容为空")
        except Exception as exc:
            input_label = self.input_mode_var.get()
            text = ""
            blocked.append(str(exc))
        try:
            voice_label, account_label, reference = self._voice_source()
            payload["account_label"] = account_label
            payload["reference_audio_path"] = reference
        except Exception as exc:
            voice_label = self.voice_mode_var.get()
            blocked.append(str(exc))
        output_dir = self.output_dir_var.get().strip()
        if not output_dir:
            blocked.append("请选择输出目录")
        preview = re.sub(r"\s+", " ", text).strip()
        sections = [
            DialogSection(
                title="单独配音范围",
                step="1",
                tone="primary",
                rows=[
                    ("输入来源", input_label),
                    ("配音方式", voice_provider_label(self._selected_voice_provider())),
                    ("音色来源", voice_label),
                    ("输出目录", output_dir),
                    ("文本长度", f"{len(text)} 字"),
                ],
                helper="MD 文档会作为一整段文字生成一条配音，不做分段切分。",
            ),
            DialogSection(
                title="文本预览",
                step="2",
                tone="info",
                items=[preview[:180] + ("..." if len(preview) > 180 else "")] if preview else ["无"],
            ),
            DialogSection(
                title="阻塞项",
                step="3",
                tone="warning" if blocked else "success",
                items=preview_lines(blocked) if blocked else [],
                helper="" if blocked else "当前没有发现阻塞项，可以继续生成配音。",
            ),
        ]
        return sections, not blocked, payload

    def _run_standalone_voice(self) -> None:
        sections, can_continue, payload = self._precheck_sections()
        if not show_precheck_dialog(
            self,
            "单独配音预检查",
            "请核对输入内容、音色来源和输出目录，确认无误后再生成。",
            sections,
            can_continue=can_continue,
            confirm_text="生成配音",
        ):
            return

        progress_dialog = TaskProgressDialog(self, "正在生成单独配音", "正在准备配音任务...")
        progress_dialog.append(f"输出目录：{self.output_dir_var.get().strip()}")
        progress_dialog.append("")

        def append_progress(message: str) -> None:
            msg = safe_text(message)
            if msg.startswith("[服务检查]"):
                progress_dialog.status_var.set("正在检查并预热配音服务...")
            elif msg.startswith("[音色]"):
                progress_dialog.status_var.set("正在确认音色来源...")
            elif msg.startswith("[成功]"):
                progress_dialog.status_var.set("正在写入音频文件...")
            progress_dialog.append(msg)

        def progress_hook(message: str) -> None:
            self.after(0, lambda m=message: append_progress(m))

        def work() -> WorkflowRunResult:
            return self.workflow.synthesize_standalone_voice(
                payload["text"],
                account_label=payload["account_label"],
                reference_audio_path=payload["reference_audio_path"],
                voice_provider=self._selected_voice_provider(),
                output_dir=self.output_dir_var.get().strip(),
                source_label=payload["source_label"],
                start_service_if_needed=True,
                progress_hook=progress_hook,
            )

        def close_service() -> None:
            if self._selected_voice_provider() == VOICE_PROVIDER_MINIMAX:
                return
            if not self.workflow.is_tts_service_running(timeout=0.8):
                return
            killed = self.workflow.shutdown_tts_service()
            if killed > 0:
                self.log(f"配音服务已关闭（{killed} 个进程）。")
                self.app.toast(f"配音服务已关闭（{killed} 个进程）", kind="info")

        def on_success(result: WorkflowRunResult) -> None:
            self.log(result.stdout or "")
            progress_dialog.append(result.stdout or "")
            progress_dialog.finish(
                "单独配音已生成完成。",
                kind="success",
                headline="配音生成完成",
                detail=self.output_dir_var.get().strip(),
            )
            self.app.toast("单独配音完成")
            close_service()

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"执行失败：{exc}", kind="error")
            messagebox.showerror("执行失败", str(exc))
            close_service()

        self.app.run_background("单独配音", work, on_success=on_success, on_error=on_error, show_success_toast=False)

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
