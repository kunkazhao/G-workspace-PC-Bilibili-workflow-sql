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

class SubtitleSrtPage(WorkflowPage):
    def __init__(self, master, app: App):
        super().__init__(master, app, "导出字幕 SRT")
        self.output_dir_var = ctk.StringVar(value=str(DEFAULT_STANDALONE_VOICE_ROOT))
        self.output_filename_var = ctk.StringVar(value="字幕-口播稿.srt")
        self.intro_video_var.trace_add("write", lambda *_args: self._sync_intro_text_state())

        form = ctk.CTkFrame(self.form_area, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        form.pack(fill="x", pady=(0, UIStyle.PAD_LG))
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.pack(fill="x", padx=UIStyle.PAD_LG, pady=UIStyle.PAD_LG)
        inner.columnconfigure(1, weight=1)

        r = 0
        ctk.CTkLabel(inner, text="口播稿 MD", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        AppEntry(inner, textvariable=self.spoken_md_var).grid(row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="选", width=50, command=self._browse_spoken_md_for_srt).grid(row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="导出目录", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        AppEntry(inner, textvariable=self.output_dir_var).grid(row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="选", width=50, command=self._browse_srt_output_dir).grid(row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="SRT 文件名", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        AppEntry(inner, textvariable=self.output_filename_var).grid(row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        GhostButton(inner, text="重置", width=50, command=lambda: self._sync_default_srt_filename(force=True)).grid(row=r, column=2, pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(inner, text="片头视频时长校准（可选）", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="w", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        AppEntry(inner, textvariable=self.intro_video_var).grid(row=r, column=1, sticky="ew", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS)
        intro_actions = ctk.CTkFrame(inner, fg_color="transparent")
        intro_actions.grid(row=r, column=2, sticky="w", pady=UIStyle.PAD_XS)
        GhostButton(intro_actions, text="选", width=50, command=self._browse_intro_video_for_srt).pack(side="left")
        GhostButton(intro_actions, text="清空", width=58, command=self._clear_intro_video_for_srt).pack(side="left", padx=(UIStyle.PAD_XS, 0))

        r += 1
        ctk.CTkLabel(inner, text="片头文案", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=r, column=0, sticky="nw", padx=(0, UIStyle.PAD_SM), pady=UIStyle.PAD_XS
        )
        self.intro_text_input = AppTextbox(inner, height=88, wrap="word")
        self.intro_text_input.grid(row=r, column=1, columnspan=2, sticky="ew", pady=UIStyle.PAD_XS)

        r += 1
        ctk.CTkLabel(
            inner,
            text="只有最终成片前面另放片头视频时才需要选择；选择后可粘贴片头文案，留空则只做时长校准。",
            font=UIStyle.FONT_SMALL,
            text_color=UIStyle.COLOR_TEXT_DIM,
        ).grid(row=r, column=0, columnspan=3, sticky="w", pady=(UIStyle.PAD_XS, UIStyle.PAD_SM))

        actions = ctk.CTkFrame(self.form_area, fg_color="transparent")
        actions.pack(fill="x", pady=(0, UIStyle.PAD_MD))
        PrimaryButton(actions, text="预检查并导出", command=self._run_subtitle_export).pack(side="right")
        self._sync_intro_text_state()

    def refresh(self) -> None:
        self.app.sync_project_selectors()
        project = self.app.current_project()
        if project:
            if self.loaded_project_id != project["id"]:
                self.spoken_md_var.set(safe_text(project.get("spoken_md_path")))
                self.loaded_project_id = project["id"]
                self._sync_default_srt_filename(force=True)

    def _browse_spoken_md_for_srt(self) -> None:
        current_path = safe_text(self.spoken_md_var.get())
        default_path = Path(current_path) if current_path else DEFAULT_SPOKEN_MD_ROOT / "口播稿.md"
        initial_dir = default_path.parent if default_path.parent.exists() else DEFAULT_SPOKEN_MD_ROOT
        path = filedialog.askopenfilename(
            title="选择口播稿 MD",
            initialdir=str(initial_dir),
            filetypes=[("Markdown", "*.md"), ("All", "*.*")],
        )
        if not path:
            return
        if Path(path).suffix.casefold() != ".md":
            self.toast("导出字幕 SRT 只支持选择 .md 口播稿。", kind="warning")
            return
        self.spoken_md_var.set(path.replace("/", "\\"))
        self._sync_default_srt_filename(force=True)

    def _browse_srt_output_dir(self) -> None:
        initial = self.output_dir_var.get().strip() or str(DEFAULT_STANDALONE_VOICE_ROOT)
        path = filedialog.askdirectory(initialdir=initial, title="选择 SRT 导出目录")
        if path:
            self.output_dir_var.set(path.replace("/", "\\"))

    def _browse_intro_video_for_srt(self) -> None:
        self._browse_intro_video()
        self._sync_intro_text_state()

    def _clear_intro_video_for_srt(self) -> None:
        self.intro_video_var.set("")
        self._sync_intro_text_state()

    def _sync_intro_text_state(self) -> None:
        intro_text = getattr(self, "intro_text_input", None)
        if intro_text is None:
            return
        intro_text.configure(state="normal" if self.intro_video_var.get().strip() else "disabled")

    def _intro_video_text(self) -> str:
        intro_text = getattr(self, "intro_text_input", None)
        if intro_text is None or not self.intro_video_var.get().strip():
            return ""
        return intro_text.get("1.0", "end").strip()

    def _sync_default_srt_filename(self, *, force: bool = False) -> None:
        current = self.output_filename_var.get().strip()
        if not force and current and not current.startswith("字幕-"):
            return
        stem = Path(self.spoken_md_var.get().strip()).stem if self.spoken_md_var.get().strip() else "口播稿"
        self.output_filename_var.set(f"字幕-{safe_file_component(stem, '口播稿')}.srt")

    def _subtitle_target_path(self) -> Path:
        filename = self.output_filename_var.get().strip()
        if not filename:
            stem = Path(self.spoken_md_var.get().strip()).stem if self.spoken_md_var.get().strip() else "口播稿"
            filename = f"字幕-{safe_file_component(stem, '口播稿')}.srt"
        if Path(filename).suffix.casefold() != ".srt":
            filename = f"{filename}.srt"
        return Path(self.output_dir_var.get().strip()) / filename

    def _subtitle_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool, dict[str, Any]]:
        blocked: list[str] = []
        warnings: list[str] = []
        path_text = self.spoken_md_var.get().strip()
        spoken_path = Path(path_text) if path_text else Path()
        manifest = self.workflow.spoken_manifest_path(project["id"], spoken_path if path_text else "口播稿.md")
        output_dir_text = self.output_dir_var.get().strip()
        output_path = self._subtitle_target_path()
        intro_video_text = self.intro_video_var.get().strip()
        intro_video = Path(intro_video_text) if intro_video_text else None
        intro_text = self._intro_video_text()

        if not path_text:
            blocked.append("请选择口播稿 MD。")
        elif spoken_path.suffix.casefold() != ".md":
            blocked.append("口播稿必须是 .md 文件。")
        elif not spoken_path.exists():
            blocked.append(f"口播稿不存在：{spoken_path}")
        if not manifest.exists():
            blocked.append(f"缺少内部 manifest，请先在“组合口播稿”生成：{manifest}")
        if not output_dir_text:
            blocked.append("请选择 SRT 导出目录。")
        else:
            anchor = Path(output_dir_text).anchor
            if anchor and not Path(anchor).exists():
                blocked.append(f"导出目录所在盘符不存在：{anchor}")
        filename = self.output_filename_var.get().strip()
        if filename and not is_valid_windows_filename(filename):
            blocked.append("SRT 文件名不能包含路径或 Windows 非法字符。")
        if intro_video is not None and not intro_video.exists():
            blocked.append(f"引言成片视频不存在：{intro_video}")
        if output_path.exists():
            warnings.append(f"目标文件已存在，确认后会覆盖：{output_path}")
        elif output_dir_text and not Path(output_dir_text).exists():
            warnings.append(f"导出目录不存在，确认后会自动创建：{output_dir_text}")

        entries: list[dict[str, Any]] = []
        missing_text: list[str] = []
        missing_audio: list[str] = []
        manifest_error = ""
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
                entries = subtitle_manifest_entries(payload)
                export_entries = entries
                if intro_video is not None:
                    export_entries = [entry for entry in entries if safe_text(entry.get("section")) != "intro"]
                for entry in export_entries:
                    label = subtitle_entry_label(entry)
                    if not safe_text(entry.get("text")).strip():
                        missing_text.append(label)
                    audio_text = safe_text(entry.get("audio_path"))
                    if not audio_text:
                        missing_audio.append(label)
                        continue
                    audio_path = Path(audio_text)
                    if not audio_path.is_absolute():
                        audio_path = manifest.parent / audio_path
                    if not audio_path.exists():
                        missing_audio.append(f"{label}：{audio_path}")
                if not export_entries:
                    blocked.append("manifest 中没有可导出的字幕条目。")
            except Exception as exc:
                manifest_error = str(exc)
                blocked.append(f"manifest 读取失败：{exc}")
        if missing_text:
            blocked.append(f"缺字幕文本 {len(missing_text)} 条。")
        if missing_audio:
            blocked.append(f"缺配音文件 {len(missing_audio)} 条。")

        sections = [
            DialogSection(
                title="导出配置",
                step="1",
                tone="primary",
                rows=[
                    ("项目", safe_text(project.get("name"))),
                    ("口播稿", str(spoken_path) if path_text else "未选择"),
                    ("manifest", str(manifest)),
                    ("导出文件", str(output_path)),
                    (
                        "字幕对齐",
                        f"独立 ASR 子进程（faster-whisper {DEFAULT_SUBTITLE_ASR_MODEL}，CPU 线程 {DEFAULT_SUBTITLE_ASR_WORKERS}）",
                    ),
                    ("片头视频时长校准", str(intro_video) if intro_video is not None else "未选择"),
                    ("片头文案", f"{len(intro_text)} 字" if intro_video is not None and intro_text else "未填写"),
                ],
            ),
            DialogSection(
                title="manifest 检查",
                step="2",
                tone="warning" if missing_text or missing_audio or manifest_error else "success",
                rows=[
                    ("字幕条目", str(len(entries))),
                    ("缺字幕文本", str(len(missing_text))),
                    ("缺配音文件", str(len(missing_audio))),
                ],
                items=preview_lines((missing_text + missing_audio)[:12]) if (missing_text or missing_audio) else [],
                helper="" if (missing_text or missing_audio) else "当前没有发现字幕文本或配音文件缺口。",
            ),
            DialogSection(
                title="阻塞与提醒",
                step="3",
                tone="warning" if warnings or blocked else "success",
                items=preview_lines(blocked + warnings) if (blocked or warnings) else [],
                helper="" if blocked else "当前没有阻塞项，可以继续导出字幕 SRT。",
            ),
        ]
        return sections, not blocked, {
            "manifest": manifest,
            "output_path": output_path,
            "intro_video": intro_video_text,
            "intro_video_text": intro_text,
            "target_exists": output_path.exists(),
        }

    def _run_subtitle_export(self) -> None:
        project = self.project_required()
        if not project:
            return
        self._sync_default_srt_filename()
        sections, can_continue, payload = self._subtitle_precheck(project)
        confirm_text = "修正后再导出" if not can_continue else ("覆盖并导出" if payload["target_exists"] else "导出 SRT")
        confirmed = show_precheck_dialog(
            self,
            "导出字幕 SRT 预检查",
            "请核对口播稿、manifest、导出路径和缺口信息，确认无误后再导出。",
            sections,
            can_continue=can_continue,
            confirm_text=confirm_text,
            dismiss_text="关闭" if not can_continue else "取消",
        )
        if not confirmed:
            if not can_continue:
                self.toast("存在阻塞项，SRT 未导出", kind="warning", duration=4000)
            return

        progress_dialog = TaskProgressDialog(self, "正在导出字幕 SRT", "正在按口播 manifest 和配音 ASR 时间生成字幕文件。")
        progress_dialog.append(f"manifest：{payload['manifest']}")
        progress_dialog.append(f"导出文件：{payload['output_path']}")
        progress_dialog.append(
            f"字幕对齐：独立 ASR 子进程（faster-whisper {DEFAULT_SUBTITLE_ASR_MODEL}，CPU 线程 {DEFAULT_SUBTITLE_ASR_WORKERS}）"
        )
        if payload["intro_video"]:
            progress_dialog.append(f"片头视频时长校准：{payload['intro_video']}")
            if payload["intro_video_text"]:
                progress_dialog.append(f"片头文案：{len(payload['intro_video_text'])} 字")
        progress_dialog.append("")

        def work() -> WorkflowRunResult:
            return self.workflow.export_subtitle_srt(
                project["id"],
                manifest_path=payload["manifest"],
                output_path=payload["output_path"],
                intro_video_path=payload["intro_video"],
                intro_video_text=payload["intro_video_text"],
                align_with_asr=True,
            )

        def on_success(result: WorkflowRunResult) -> None:
            self.log(result.stdout or "")
            progress_dialog.append(result.stdout or "")
            progress_dialog.finish(
                "字幕 SRT 已导出",
                kind="success",
                headline="导出完成",
                detail=result.stdout.strip(),
            )
            self.toast("字幕 SRT 已导出")

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"导出失败：{exc}", kind="error")
            messagebox.showerror("导出失败", str(exc))

        self.app.run_background("导出字幕 SRT", work, on_success=on_success, on_error=on_error, show_success_toast=False)
