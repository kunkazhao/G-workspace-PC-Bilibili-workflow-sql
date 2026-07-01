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


class WorkflowPage(BasePage):
    def __init__(self, master, app: App, title: str):
        super().__init__(master, title, app)
        self.mode_var = ctk.StringVar(value="standard")
        self.project_var = app.project_selector_var
        self.account_var = ctk.StringVar()
        self.uid_var = ctk.StringVar()
        self.intro_var = ctk.StringVar(value="1")
        self.intro_choice_var = ctk.StringVar()
        self.spoken_md_var = ctk.StringVar()
        self.intro_video_var = ctk.StringVar()
        self.loaded_project_id: int | None = None

        # Project selector
        sel = ctk.CTkFrame(self.content, fg_color=UIStyle.COLOR_CARD_BG, corner_radius=UIStyle.RADIUS_LG)
        sel.pack(fill="x", pady=(0, UIStyle.PAD_SM))
        sel.columnconfigure(1, weight=1)
        ctk.CTkLabel(sel, text="本次品类项目", font=UIStyle.FONT_BODY, text_color=UIStyle.COLOR_TEXT_DIM).grid(
            row=0, column=0, sticky="w", padx=(UIStyle.PAD_LG, UIStyle.PAD_SM), pady=UIStyle.PAD_MD
        )
        self.project_combo = AppComboBox(sel, width=400, variable=self.project_var)
        self.project_combo.grid(row=0, column=1, sticky="ew", padx=(0, UIStyle.PAD_LG), pady=UIStyle.PAD_MD)
        self.app.register_project_selector(self.project_combo)
        self.project_combo.configure(command=self._select_project)

        # 子页面的表单控件应打包到此 frame 中（在 log 之前）
        self.form_area = ctk.CTkFrame(self.content, fg_color="transparent")
        self.form_area.pack(fill="x")

        ctk.CTkLabel(
            self.content, text="执行日志", font=UIStyle.FONT_H3,
            text_color=UIStyle.COLOR_TEXT_DIM, anchor="w",
        ).pack(anchor="w", pady=(UIStyle.PAD_SM, UIStyle.PAD_XS))
        self.log_text = AppTextbox(self.content, height=160)
        self.log_text.pack(fill="both", expand=True)

    def _is_voice_page(self) -> bool:
        return self.page_title == "生成配音"

    def _is_assemble_page(self) -> bool:
        return self.page_title == "组合口播稿"

    def _is_jianying_page(self) -> bool:
        return self.page_title == "生成剪映草稿"

    def _command(self) -> list[str]:
        project = self.project_required()
        if not project:
            return []
        if self._is_voice_page():
            uids, script_ids = parse_voice_targets(self.uid_var.get())
            return self.workflow.build_voice_command(
                project["id"],
                account_label=self.account_var.get().strip(),
                voice_provider=self._selected_voice_provider(),
                uids=uids or None,
                script_ids=script_ids or None,
            )
        if self._is_assemble_page():
            top_uids = parse_uid_list(self.uid_var.get())
            mode = "top" if self.mode_var.get().strip().startswith("Top") else "standard"
            return self.workflow.build_assembly_command(
                project["id"], mode=mode, top_uids=top_uids or None,
                account_label=self.account_var.get().strip(), intro_index=int(self.intro_var.get() or "1"),
                output_markdown_path=self._remember_spoken_md(project["id"]),
                display_template=self._display_template_for_account(),
            )
        return self.workflow.build_jianying_command(
            project["id"], draft_name=self.account_var.get().strip(),
            spoken_markdown_path=self._remember_spoken_md(project["id"]),
            intro_video_path=self.intro_video_var.get().strip(),
        )

    def _browse_spoken_md(self) -> None:
        p = self.app.current_project()
        current_path = safe_text(self.spoken_md_var.get())
        if not current_path and p:
            current_path = str(default_spoken_markdown_path(p, self.account_var.get().strip()))
        default_path = Path(current_path) if current_path else DEFAULT_SPOKEN_MD_ROOT / "口播稿.md"
        dialog_options = {
            "defaultextension": ".md",
            "filetypes": [("Markdown", "*.md"), ("All", "*.*")],
            "initialdir": str(default_path.parent),
            "initialfile": default_path.name,
        }
        if self._is_jianying_page():
            path = filedialog.askopenfilename(**dialog_options)
        else:
            path = filedialog.asksaveasfilename(confirmoverwrite=False, **dialog_options)
        if path:
            self.spoken_md_var.set(path.replace("/", "\\"))
            if self._is_jianying_page():
                self._update_jianying_draft_name(force=True)

    def _browse_intro_video(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", "*.*")], initialdir=r"G:\2026项目-b站")
        if path:
            self.intro_video_var.set(path.replace("/", "\\"))

    def _remember_spoken_md(self, project_id: int) -> str:
        path = self.spoken_md_var.get().strip()
        if path:
            self.db.execute("UPDATE projects SET spoken_md_path=?, updated_at=? WHERE id=?", (path, now_iso(), project_id))
        return path

    def _set_default_spoken_md_if_needed(self, project: dict[str, Any] | None, *, force: bool = False) -> None:
        if not project:
            return
        account_label = self.account_var.get().strip()
        current = self.spoken_md_var.get().strip()
        if force or not current or is_default_spoken_markdown_path(current):
            self.spoken_md_var.set(str(default_spoken_markdown_path(project, account_label)).replace("/", "\\"))

    def _manifest_account_label_for_current_md(self, project: dict[str, Any] | None) -> str:
        if not project:
            return ""
        md_text = self.spoken_md_var.get().strip()
        if md_text:
            try:
                manifest = self.workflow.spoken_manifest_path(project["id"], md_text)
                if manifest.exists():
                    payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
                    label = manifest_account_label(payload)
                    if label:
                        return label
            except Exception:
                pass
        return account_label_from_spoken_path(md_text)

    def _update_jianying_draft_name(self, *, force: bool = False) -> None:
        project = self.app.current_project()
        if not project:
            return
        current = self.account_var.get().strip()
        if current and not force and not current.startswith("完整-"):
            return
        label = self._manifest_account_label_for_current_md(project)
        self.account_var.set(default_jianying_draft_name(project, label))

    def project_required(self) -> dict[str, Any] | None:
        p = self.app.current_project()
        if not p:
            self.toast("请先在“品类项目”中创建或选择项目。", kind="warning")
        return p

    def log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_intro_choices(self, project: dict[str, Any] | None) -> None:
        combo = getattr(self, "intro_combo", None)
        if combo is None:
            return
        choices: list[str] = []
        if project:
            for idx, b in enumerate(self.repo.script_blocks(project["id"]), start=1):
                if b["script_type"] == "intro":
                    choices.append(f"{idx} - {safe_text(b.get('block_label')) or '引言'}")
        combo.configure(values=choices)
        if choices:
            current = self.intro_choice_var.get()
            if current not in choices:
                wanted = max(1, int(self.intro_var.get() or "1"))
                self.intro_choice_var.set(choices[min(wanted, len(choices)) - 1])
            self._sync_intro_index()
        else:
            self.intro_choice_var.set("")
            self.intro_var.set("1")

    def _sync_intro_index(self) -> None:
        match = re.match(r"\s*(\d+)", self.intro_choice_var.get())
        self.intro_var.set(match.group(1) if match else "1")

    def _build_command(self) -> None:
        try:
            cmd = self._command()
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))
            return
        self.log(" ".join(f'"{p}"' if " " in p else p for p in cmd))

    def _run_command(self) -> None:
        if self._is_voice_page():
            self._run_voice_command()
            return
        try:
            confirmed = self._confirm_precheck()
        except Exception as exc:
            self.log(traceback.format_exc())
            messagebox.showerror("预检查失败", str(exc), parent=self)
            return
        if not confirmed:
            return
        try:
            cmd = self._command()
        except Exception as exc:
            messagebox.showerror("执行失败", str(exc))
            return
        progress_dialog = TaskProgressDialog(self, self._running_dialog_title(), self._running_dialog_message())
        self._append_run_summary(progress_dialog, cmd)
        progress_dialog.append("")

        def work() -> Any:
            return self.workflow.run_command(cmd)

        def on_success(result: Any) -> None:
            self.log(result.stdout or "")
            if result.stderr:
                self.log(result.stderr)
            self.log(f"退出码：{result.returncode}")
            progress_dialog.append(result.stdout or "")
            if result.stderr:
                progress_dialog.append(result.stderr)
            progress_dialog.append(f"退出码：{result.returncode}")
            if result.returncode == 0:
                progress_dialog.finish(
                    self._success_dialog_message(),
                    kind="success",
                    headline=self._success_dialog_headline(),
                    detail=self._success_dialog_detail(),
                )
                self.toast("执行完成")
            else:
                progress_dialog.finish(
                    f"执行结束，退出码：{result.returncode}",
                    kind="warning",
                    headline="执行结束",
                )
                self.toast(f"执行结束，退出码：{result.returncode}", kind="warning", duration=4500)

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"执行失败：{exc}", kind="error")
            messagebox.showerror("执行失败", str(exc))

        self.app.run_background("执行任务", work, on_success=on_success, on_error=on_error, show_success_toast=False)

    def _append_run_summary(self, progress_dialog: TaskProgressDialog, cmd: list[str]) -> None:
        if self._is_jianying_page():
            draft_name = self.account_var.get().strip()
            spoken_md = self.spoken_md_var.get().strip()
            intro_video = self.intro_video_var.get().strip()
            progress_dialog.append("已确认配置，开始生成剪映草稿。")
            if draft_name:
                progress_dialog.append(f"草稿名称：{draft_name}")
            if spoken_md:
                progress_dialog.append(f"口播稿：{spoken_md}")
            if intro_video:
                progress_dialog.append(f"引言成片视频：{intro_video}")
            return
        progress_dialog.append("即将执行：")
        progress_dialog.append(" ".join(f'"{p}"' if " " in p else p for p in cmd))

    def _run_voice_command(self) -> None:
        project = self.project_required()
        if not project:
            return
        if not self._confirm_precheck():
            return

        tasks = self._voice_tasks()
        task_counts: list[tuple[VoiceTaskDraft, int, int, int]] = []
        total_jobs = existing_jobs = pending_jobs = 0
        for task in tasks:
            uids, script_ids = parse_voice_targets(task.target_text)
            counts = self.workflow.voice_generation_counts(
                task.project_id,
                account_label=task.account_label,
                uids=uids or None,
                script_ids=script_ids or None,
            )
            task_total, task_existing, task_pending = counts
            task_counts.append((task, task_total, task_existing, task_pending))
            total_jobs += task_total
            existing_jobs += task_existing
            pending_jobs += task_pending
        if pending_jobs == 0:
            self.toast("所有文案已有 OK 配音，无需生成。", kind="info", duration=3000)
            return

        needs_local_service = any(normalize_voice_provider(task.voice_provider) == VOICE_PROVIDER_INDEXTTS for task, *_ in task_counts)
        if needs_local_service:
            service_ok = self.workflow.is_tts_service_running(timeout=0.8)
            if service_ok:
                if not show_confirmation_dialog(
                    self,
                    "配音服务已就绪",
                    "检测到本地配音服务正在运行。",
                    [DialogSection(title="服务状态", step="1", tone="success", items=["本地配音服务已在运行，可以直接继续生成配音。"])],
                    confirm_text="继续生成",
                ):
                    self.toast("已取消本次配音生成。", kind="warning")
                    return
            else:
                if not show_confirmation_dialog(
                    self,
                    "配音服务未启动",
                    "检测到本地配音服务尚未启动。",
                    [DialogSection(title="服务状态", step="1", tone="warning", items=["IndexTTS 本地配音前需要先启动并预热服务。", "MiniMax API 任务不会启动本地服务。"])],
                    confirm_text="启动并继续",
                ):
                    self.toast("已取消本次配音生成。", kind="warning")
                    return

        progress_dialog = TaskProgressDialog(self, "正在生成配音", "正在准备配音任务...")
        progress_dialog.append("配音参数：")
        progress_dialog.append(f"任务数：{len(task_counts)} 个")
        progress_dialog.append(f"本次文案：{total_jobs} 条；已有跳过：{existing_jobs} 条；待生成：{pending_jobs} 条")
        for index, (task, task_total, task_existing, task_pending) in enumerate(task_counts, start=1):
            progress_dialog.append(
                f"任务 {index}：{task.project_name}｜{task.account_label}｜{task.display_target}"
                f"｜{voice_provider_label(task.voice_provider)}｜文案 {task_total} 条，已有 {task_existing} 条，待生成 {task_pending} 条"
            )
        progress_dialog.append("")

        def append_progress(message: str) -> None:
            msg = safe_text(message)
            if msg.startswith("[服务检查]"):
                progress_dialog.status_var.set("正在检查并预热配音服务...")
            elif msg.startswith("[音色注册]"):
                progress_dialog.status_var.set("正在确认音色配置...")
            elif msg.startswith("[生成 "):
                progress_dialog.status_var.set("正在生成配音中...")
            elif msg.startswith("[成功]"):
                progress_dialog.status_var.set("正在写入并确认音频文件...")
            elif msg.startswith("[失败]"):
                progress_dialog.status_var.set("配音中存在失败条目，正在继续后续任务...")
            progress_dialog.append(msg)

        def progress_hook(message: str) -> None:
            self.after(0, lambda m=message: append_progress(m))

        cancel_event = progress_dialog.cancel_event

        def work() -> Any:
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            returncode = 0
            for index, (task, _task_total, _task_existing, task_pending) in enumerate(task_counts, start=1):
                if cancel_event.is_set():
                    progress_hook(f"[取消] 用户取消，跳过剩余任务。")
                    break
                if task_pending == 0:
                    stdout_parts.append(f"[任务 {index}] {task.project_name} / {task.account_label}：全部已有配音，跳过生成。\n")
                    continue
                progress_hook(f"[任务 {index}/{len(task_counts)}] {task.project_name} / {task.account_label} / {task.display_target}")
                uids, script_ids = parse_voice_targets(task.target_text)
                result = self.workflow.generate_voice(
                    task.project_id,
                    account_label=task.account_label,
                    voice_provider=task.voice_provider,
                    uids=uids or None,
                    script_ids=script_ids or None,
                    output_dir=task.output_dir,
                    start_service_if_needed=True,
                    progress_hook=progress_hook,
                    cancel_event=cancel_event,
                )
                stdout_parts.append(f"[任务 {index}] {task.project_name} / {task.account_label}\n{result.stdout or ''}")
                if result.stderr:
                    stderr_parts.append(f"[任务 {index}] {result.stderr}")
                if result.returncode != 0:
                    returncode = result.returncode
            return WorkflowRunResult(
                ["internal:voice-batch"],
                returncode=returncode,
                stdout="\n".join(stdout_parts),
                stderr="\n".join(stderr_parts),
            )

        def close_service() -> None:
            if not needs_local_service:
                return
            if not self.workflow.is_tts_service_running(timeout=0.8):
                return
            killed = self.workflow.shutdown_tts_service()
            if killed > 0:
                self.toast(f"配音已完成，已自动关闭配音服务（{killed} 个进程）。", kind="info")

        def on_success(result: Any) -> None:
            self.log(result.stdout or "")
            if result.stderr:
                self.log(result.stderr)
            self.log(f"退出码：{result.returncode}")
            progress_dialog.append(result.stdout or "")
            if result.stderr:
                progress_dialog.append(result.stderr)
            progress_dialog.append(f"退出码：{result.returncode}")
            if cancel_event.is_set():
                progress_dialog.finish(
                    "配音已取消，已生成的文件保留在目标目录。",
                    kind="warning",
                    headline="配音已取消",
                )
                self.toast("配音已取消", kind="warning")
            elif result.returncode == 0:
                progress_dialog.finish(
                    "本次配音已经完成，生成结果已写入目标目录。",
                    kind="success",
                    headline="配音生成完成",
                    detail=f"任务数：{len(task_counts)}",
                )
                self.toast("配音完成")
            else:
                progress_dialog.finish(
                    f"配音结束，退出码：{result.returncode}",
                    kind="warning",
                    headline="配音执行结束",
                )
                self.toast(f"配音结束，退出码：{result.returncode}", kind="warning", duration=4500)
            close_service()

        def on_error(exc: Exception, tb: str) -> None:
            progress_dialog.append(tb or str(exc))
            progress_dialog.finish(f"执行失败：{exc}", kind="error")
            messagebox.showerror("执行失败", str(exc))
            close_service()

        self.app.run_background("生成配音", work, on_success=on_success, on_error=on_error, show_success_toast=False)

    def _running_dialog_title(self) -> str:
        if self._is_jianying_page():
            return "正在生成剪映草稿"
        if self._is_voice_page():
            return "正在生成配音"
        if self._is_assemble_page():
            return "正在组合口播稿"
        return "正在执行任务"

    def _running_dialog_message(self) -> str:
        if self._is_jianying_page():
            return "通常需要几分钟。窗口会在执行结束后显示结果。"
        if self._is_voice_page():
            return "正在准备配音任务与服务状态，请等待当前任务结束后再继续操作。"
        if self._is_assemble_page():
            return "正在组合口播稿内容，请等待当前任务结束后再继续操作。"
        return "任务正在执行中，请等待当前任务结束后再继续操作。"

    def _success_dialog_headline(self) -> str:
        if self._is_jianying_page():
            return "剪映草稿生成成功"
        if self._is_assemble_page():
            return "口播稿组合完成"
        return "执行完成"

    def _success_dialog_message(self) -> str:
        if self._is_jianying_page():
            return "草稿已经写入输出目录，现在可以去剪映里打开。"
        if self._is_assemble_page():
            return "口播稿与 manifest 已生成完成，可以继续后续流程。"
        return "任务已经完成，可以关闭窗口。"

    def _success_dialog_detail(self) -> str:
        if self._is_jianying_page():
            draft_name = self.account_var.get().strip() or safe_text(self.project_required().get("name"))
            return f"草稿名称：{draft_name}"
        if self._is_assemble_page():
            output_path = self.spoken_md_var.get().strip()
            return f"输出文件：{output_path}" if output_path else ""
        return ""

    def _confirm_precheck(self) -> bool:
        project = self.project_required()
        if not project:
            return False
        if self._is_voice_page():
            sections, can_continue = self._voice_precheck(project)
            return show_precheck_dialog(
                self,
                "生成配音预检查",
                "请核对本次配音范围、已有配音状态与阻塞项，确认无误后再继续生成。",
                sections,
                can_continue=can_continue,
            )
        if self._is_jianying_page():
            sections, can_continue = self._jianying_precheck(project)
            return show_precheck_dialog(
                self,
                "生成剪映草稿预检查",
                "请核对以下配置信息，确认无误后再生成草稿。",
                sections,
                can_continue=can_continue,
            )
        if self._is_assemble_page():
            sections, can_continue = self._assembly_precheck(project)
            return show_precheck_dialog(
                self,
                "组合口播稿预检查",
                "请核对组合范围、素材缺口与阻塞问题，确认无误后再继续生成。",
                sections,
                can_continue=can_continue,
            )
        return True

    def _voice_task_precheck_sections(
        self,
        project: dict[str, Any],
        *,
        account_label: str,
        voice_provider: str = VOICE_PROVIDER_INDEXTTS,
        target_text: str,
        output_dir_text: str = "",
        task_title: str = "",
        step_start: int = 1,
    ) -> tuple[list[DialogSection], dict[str, int], bool]:
        selected_uids, selected_script_ids = parse_voice_targets(target_text)
        try:
            self.sync.sync_markdown(project["id"])
        except Exception as exc:
            prefix = f"{task_title}｜" if task_title else ""
            return [
                DialogSection(
                    title=f"{prefix}MD 同步失败",
                    step=str(step_start),
                    tone="warning",
                    items=[f"配音预检查前同步当前 MD 失败：{exc}"],
                )
            ], {"pending": 0, "skipped": 0, "blocked": 1}, False
        products = {a["uid"]: a for a in self.repo.products(project["id"], include_removed=False)}
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        selected = set(selected_uids)
        selected_scripts = {item.casefold() for item in selected_script_ids}
        unknown = [u for u in selected_uids if u not in products]
        unknown_scripts = [
            script_id
            for script_id in selected_script_ids
            if script_id.casefold() not in {safe_text(block.get("script_id")).casefold() for block in blocks}
        ]
        product_blocks = [
            b for b in blocks
            if b["script_type"] == "product"
            and (
                (not selected and not selected_scripts)
                or b["owner_uid"] in selected
                or safe_text(b.get("script_id")).casefold() in selected_scripts
            )
        ]
        shared_blocks = [
            b for b in blocks
            if b["script_type"] in {"intro", "price_transition"}
            and (
                (not selected and not selected_scripts)
                or safe_text(b.get("script_id")).casefold() in selected_scripts
            )
        ]
        pending, skipped, blocked = [], [], []
        for uid in selected_uids:
            if uid in products and not any(b["owner_uid"] == uid for b in product_blocks):
                blocked.append(f"{uid} {products[uid]['title']}：缺文案")
        for uid in unknown:
            blocked.append(f"{uid}：当前品类项目中没有这个商品")
        for script_id in unknown_scripts:
            blocked.append(f"{script_id}：当前品类项目中没有这个文案版本 ID")
        for b in product_blocks:
            prod = products.get(b["owner_uid"], {})
            display = f"{safe_text(b.get('script_id'))} / {b['owner_uid']} {safe_text(prod.get('title'))} / {b['block_label']}"
            state = voice_state(assets, uid=b["owner_uid"], account_label=account_label, hashes={b["text_hash"]}, block_label=safe_text(b.get("block_label")))
            (pending if state != "ready" else skipped).append(f"{display}：{'配音过期，将重生成' if state == 'expired' else '缺配音，将生成'}" if state != "ready" else f"{display}：已有配音")
        for b in shared_blocks:
            uid = "INTRO" if b["script_type"] == "intro" else "PRICE_TRANSITION"
            label = safe_text(b.get("block_label")) if uid == "INTRO" else safe_text(b.get("price_range_label"))
            kind_label = "引言文案" if uid == "INTRO" else f"价格过渡 {safe_text(b.get('price_range_label'))}"
            display = f"{safe_text(b.get('script_id'))} / {kind_label} / {b['block_label']}"
            state = voice_state(assets, uid=uid, account_label=account_label, hashes={b["text_hash"]}, block_label=label)
            (pending if state != "ready" else skipped).append(f"{display}：{'配音过期，将重生成' if state == 'expired' else '缺配音，将生成'}" if state != "ready" else f"{display}：已有配音")
        selected_text = "全部文案" if not selected_uids and not selected_script_ids else "、".join(selected_uids + selected_script_ids)
        output_dir = None
        if account_label:
            try:
                output_dir = Path(output_dir_text) if safe_text(output_dir_text) else self.workflow.expected_voice_output_dir(project["id"], account_label=account_label)
            except Exception as exc:
                blocked.append(f"保存路径无法计算：{exc}")
        prefix = f"{task_title}｜" if task_title else ""
        sections = [
            DialogSection(
                title=f"{prefix}项目信息",
                step=str(step_start),
                tone="primary",
                rows=[
                    ("项目", project["name"]),
                    ("配音用户", account_label or "未选择"),
                    ("配音方式", voice_provider_label(voice_provider)),
                    ("生成范围", selected_text),
                    ("保存路径", str(output_dir) if output_dir else "未选择用户"),
                ],
            ),
            DialogSection(
                title=f"{prefix}执行统计",
                step=str(step_start + 1),
                tone="success",
                rows=[
                    ("待生成 / 重生成", f"{len(pending)} 条"),
                    ("已有配音跳过", f"{len(skipped)} 条"),
                    ("缺文案 / 不可处理", f"{len(blocked)} 条"),
                ],
                helper="确认后会先执行底层脚本；已有配音由脚本继续跳过，缺失和过期会重新生成。",
            ),
            DialogSection(
                title=f"{prefix}待生成明细",
                step=str(step_start + 2),
                tone="info",
                items=preview_lines(pending) if pending else [],
                helper="" if pending else "当前没有需要生成或重生成的配音。",
            ),
            DialogSection(
                title=f"{prefix}阻塞与缺口",
                step=str(step_start + 3),
                tone="warning" if blocked else "success",
                items=preview_lines(blocked) if blocked else [],
                helper="" if blocked else "当前没有发现阻塞项，可以继续生成配音。",
            ),
        ]
        stats = {"pending": len(pending), "skipped": len(skipped), "blocked": len(blocked)}
        return sections, stats, bool(account_label) and bool(pending or skipped or blocked)

    def _voice_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool]:
        sections, _stats, can_continue = self._voice_task_precheck_sections(
            project,
            account_label=self.account_var.get().strip(),
            voice_provider=self._selected_voice_provider() if self._is_voice_page() else VOICE_PROVIDER_INDEXTTS,
            target_text=self.uid_var.get(),
        )
        return sections, can_continue

    def _assembly_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool]:
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        top_uids = parse_uid_list(self.uid_var.get())
        account_label = self.account_var.get().strip()
        mode = "top" if self.mode_var.get().strip().startswith("Top") else "standard"
        display_template = self._display_template_for_account()
        products = self.workflow._ordered_products(project["id"], mode=mode, top_uids=top_uids, product_uids=[])
        product_blocks = [block for block in blocks if block["script_type"] == "product"]
        intro_blocks = [block for block in blocks if block["script_type"] == "intro"]
        price_blocks = [block for block in blocks if block["script_type"] == "price_transition"]
        product_block_uids = {safe_text(block.get("owner_uid")).casefold() for block in product_blocks}
        missing_top = [uid for uid in top_uids if uid.casefold() not in product_block_uids]
        product_blocks_by_uid: dict[str, list[dict[str, Any]]] = {}
        for block in product_blocks:
            product_blocks_by_uid.setdefault(safe_text(block.get("owner_uid")), []).append(block)
        top_set = {uid.casefold() for uid in top_uids}
        ordered_blocks: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        used_price_labels: set[str] = set()
        used_price_blocks: list[dict[str, Any]] = []
        for product in products:
            uid = safe_text(product.get("uid"))
            is_top_product = uid.casefold() in top_set
            if not is_top_product:
                price_block = self.workflow._matching_price_block_for_assets(product, price_blocks, assets, account_label=account_label)
                if price_block:
                    price_key = safe_text(price_block.get("price_range_label")) or str(price_block["id"])
                    if price_key not in used_price_labels:
                        used_price_labels.add(price_key)
                        used_price_blocks.append(price_block)
            versions = product_blocks_by_uid.get(uid, [])
            if versions:
                block = self.workflow._choose_voice_ready_block(versions, assets, uid=uid, account_label=account_label) or versions[0]
                ordered_blocks.append((block, product, is_top_product))
        top_product_blocks = [item for item in ordered_blocks if item[2]]
        other_product_blocks = [item for item in ordered_blocks if not item[2]]
        selected_products = [product for product in products if safe_text(product.get("uid")) in product_blocks_by_uid]
        expected_blocks = min(1, len(intro_blocks)) + len(used_price_blocks) + len(ordered_blocks)
        missing_voice = []
        missing_image = []
        missing_video = []
        image_template_suffix = image_set_for_template(display_template)
        display_image_user = account_label if image_template_suffix else ""
        for block, product, _is_top_product in ordered_blocks:
            uid = safe_text(block.get("owner_uid"))
            label = f"{uid} {safe_text(product.get('title'))}".strip()
            if voice_state(assets, uid=uid, account_label=account_label, hashes={safe_text(block.get("text_hash"))}) != "ready":
                missing_voice.append(label)
            if not has_ready_asset(
                assets,
                uid=uid,
                asset_type="image",
                account_label=display_image_user,
                path_contains=image_template_suffix,
                allow_global_account=not bool(image_template_suffix),
            ):
                missing_image.append(label)
            if not has_ready_asset(assets, uid=uid, asset_type="video"):
                missing_video.append(label)
        selected_intro = []
        if intro_blocks:
            intro_index = max(1, int(self.intro_var.get() or "1"))
            selected_intro = [intro_blocks[min(intro_index, len(intro_blocks)) - 1]]
        for block in selected_intro:
            if voice_state(
                assets,
                uid="INTRO",
                account_label=account_label,
                hashes={safe_text(block.get("text_hash"))},
                block_label=safe_text(block.get("block_label")),
            ) != "ready":
                missing_voice.append("引言文案")
        for block in used_price_blocks:
            if voice_state(
                assets,
                uid="PRICE_TRANSITION",
                account_label=account_label,
                hashes={safe_text(block.get("text_hash"))},
                block_label=safe_text(block.get("price_range_label")),
            ) != "ready":
                missing_voice.append(f"价格过渡 {safe_text(block.get('price_range_label'))}")
        output_path = self._remember_spoken_md(project["id"]) if self.spoken_md_var.get().strip() else safe_text(project.get("spoken_md_path"))
        voice_scope = self.workflow._voice_scope_fragment(project, account_label)
        asset_entries: list[dict[str, Any]] = []
        asset_order = 1
        for block in selected_intro:
            asset_entries.append(
                self.workflow._manifest_entry(
                    order=asset_order,
                    entry_type="transition",
                    section="intro",
                    block=block,
                    account_label=account_label,
                    account_id="",
                    assets=assets,
                    product={},
                    source_label=safe_text(block.get("block_label")),
                    display_template=display_template,
                    preferred_voice_path_contains=voice_scope,
                )
            )
            asset_order += 1
        for block in used_price_blocks:
            asset_entries.append(
                self.workflow._manifest_entry(
                    order=asset_order,
                    entry_type="transition",
                    section="price_transition",
                    block=block,
                    account_label=account_label,
                    account_id="",
                    assets=assets,
                    product={},
                    source_label=f"价格过渡 {safe_text(block.get('price_range_label'))}",
                    display_template=display_template,
                    preferred_voice_path_contains=voice_scope,
                )
            )
            asset_order += 1
        for block, product, is_top_product in ordered_blocks:
            asset_entries.append(
                self.workflow._manifest_entry(
                    order=asset_order,
                    entry_type="product",
                    section="top" if is_top_product else "product",
                    block=block,
                    account_label=account_label,
                    account_id="",
                    assets=assets,
                    product=product,
                    source_label=safe_text(block.get("block_label")),
                    display_template=display_template,
                    preferred_voice_path_contains=voice_scope,
                )
            )
            asset_order += 1
        asset_issue_items = entry_asset_issue_lines(asset_entries)
        blockers: list[str] = []
        if not output_path:
            blockers.append("还没有选择口播稿输出 MD。")
        if not account_label:
            blockers.append("还没有选择口播用户。")
        if not blocks:
            blockers.append("当前项目还没有同步到任何文案块。请先到“同步中心”同步商品文案 MD。")
        if top_uids and not top_product_blocks:
            blockers.append("填写的 Top UID 没有匹配到任何商品文案。")
        if missing_top:
            blockers.append(f"这些 Top UID 没有对应文案：{'、'.join(missing_top)}")
        gap_items: list[str] = []
        if missing_voice:
            gap_items.append("缺配音：" + "；".join(missing_voice[:5]))
        if missing_image:
            gap_items.append("缺图片：" + "；".join(missing_image[:5]))
        if missing_video:
            gap_items.append("缺视频：" + "；".join(missing_video[:5]))
        sections = [
            DialogSection(
                title="项目信息",
                step="1",
                tone="primary",
                rows=[
                    ("项目", project["name"]),
                    ("用户", account_label or "未选择"),
                    ("模式", self.mode_var.get()),
                    ("Top 商品", "、".join(top_uids) if top_uids else "未填写，将使用全部商品"),
                    ("输出 MD", output_path or "未选择"),
                ],
            ),
            DialogSection(
                title="组合范围",
                step="2",
                tone="warning" if asset_issue_items else "success",
                rows=[
                    ("预计段落", f"约 {expected_blocks + 1} 段（引言 {len(selected_intro)}，价格过渡 {len(used_price_blocks)}，商品文案 {len(ordered_blocks)}，结尾 1）"),
                    ("商品范围", f"共 {len(selected_products)} 个；Top 命中文案 {len(top_product_blocks)} 条；其他商品文案 {len(other_product_blocks)} 条"),
                    ("素材缺口", f"缺配音 {len(missing_voice)}，缺图片 {len(missing_image)}，缺视频 {len(missing_video)}"),
                ],
                items=preview_lines(asset_issue_items, limit=80) if asset_issue_items else [],
                helper="这里只显示缺配音、缺图片、缺视频或路径不存在的记录；正常匹配不再展开。",
            ),
            DialogSection(
                title="缺口示例",
                step="3",
                tone="warning" if gap_items else "success",
                items=gap_items or ["当前没有明显素材缺口。"],
            ),
            DialogSection(
                title="阻塞问题",
                step="4",
                tone="warning" if blockers else "success",
                items=blockers,
                helper="" if blockers else "当前没有阻塞问题，可以继续组合口播稿。",
            ),
        ]
        return sections, not blockers

    def _jianying_precheck(self, project: dict[str, Any]) -> tuple[list[DialogSection], bool]:
        path_text = self.spoken_md_var.get().strip() or safe_text(project.get("spoken_md_path"))
        if not path_text:
            return [
                DialogSection(
                    title="阻塞问题",
                    step="1",
                    tone="error",
                    items=["还没有选择口播稿 MD。", "请先在“组合口播稿”生成口播稿和 manifest。"],
                )
            ], False
        spoken_path = Path(path_text)
        manifest = self.workflow.spoken_manifest_path(project["id"], spoken_path)
        intro_video_text = self.intro_video_var.get().strip()
        intro_video_path = Path(intro_video_text) if intro_video_text else None
        missing_manifest = not manifest.exists()
        bg_dir = Path(r"G:\2026项目-b站\素材-剪辑\1-背景图")
        bg_images = list(bg_dir.glob("*")) if bg_dir.exists() else []
        bg_image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
        has_bg = any(p.is_file() and p.suffix.casefold() in bg_image_suffixes for p in bg_images)
        products = self.repo.products(project["id"], include_removed=False)
        blocks = self.repo.script_blocks(project["id"])
        assets = self.repo.asset_bindings(project["id"])
        missing_files, missing_product_videos = [], []
        manifest_error = ""
        selected_user = "全部"
        display_template = ""
        payload: dict[str, Any] = {}
        entries: list[dict[str, Any]] = []
        missing_by_type: dict[str, list[str]] = {"audio": [], "image": [], "video": []}
        if manifest.exists():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
                entries = manifest_entries(payload)
                selected_user = manifest_account_label(payload) or selected_user
                display_template = manifest_display_template(payload)
                effective_payload = dict(payload) if isinstance(payload, dict) else {"entries": entries}
                if intro_video_path is not None:
                    effective_payload["entries"] = [
                        entry for entry in entries if safe_text(entry.get("section")) != "intro"
                    ]
                    entries = manifest_entries(effective_payload)
                missing_product_videos = manifest_product_video_gaps(effective_payload)
                missing_by_type = manifest_missing_assets(effective_payload)
                for p in manifest_file_paths(effective_payload):
                    if not Path(p).exists():
                        missing_files.append(p)
            except Exception as exc:
                manifest_error = str(exc)
        issues = build_project_issue_summary(project, products, blocks, assets, self.repo.accounts(), selected_user=selected_user, image_template=display_template)
        entry_counts = {
            "transition": sum(1 for item in entries if safe_text(item.get("type")) == "transition"),
            "product": sum(1 for item in entries if safe_text(item.get("type")) == "product"),
            "closing": sum(1 for item in entries if safe_text(item.get("type")) == "closing"),
        }
        product_names = [
            " ".join(part for part in [safe_text(item.get("product_uid")), safe_text(item.get("product_name"))] if part)
            for item in entries
            if safe_text(item.get("type")) == "product"
        ]
        result_items: list[str] = []
        if not has_bg:
            result_items.append("背景图：缺失，G:\\2026项目-b站\\素材-剪辑\\1-背景图 目录下没有可用图片。")
        if missing_manifest:
            result_items.append("manifest：缺失，还没有组合口播稿，不能生成剪映草稿。")
        if manifest_error:
            result_items.append(f"manifest 读取失败：{manifest_error}")
        if intro_video_path is not None and not intro_video_path.exists():
            result_items.append(f"引言成片视频不存在：{intro_video_path}")
        if missing_files:
            result_items.append(f"manifest 中有 {len(missing_files)} 个文件路径不存在；缺音频会阻塞生成，缺图片/视频会尝试兜底")
        if missing_by_type["audio"]:
            result_items.append(f"缺配音：manifest 中有 {len(missing_by_type['audio'])} 条已选文案没有音频，请先生成或同步这些配音。")
        if missing_by_type["image"]:
            result_items.append(f"manifest 中有 {len(missing_by_type['image'])} 条图片路径缺失；会尝试用数据库素材或兜底图处理")
        if missing_by_type["video"]:
            result_items.append(f"manifest 中有 {len(missing_by_type['video'])} 条视频路径缺失；商品展示视频缺失时会用商品图兜底")
        if missing_product_videos:
            result_items.append(f"{len(missing_product_videos)} 个商品没有展示视频，将用商品图兜底")
        asset_issue_items = entry_asset_issue_lines(entries)
        if intro_video_path is not None and not intro_video_path.exists():
            asset_issue_items.insert(0, f"引言视频路径不存在：{intro_video_path}")
        sections = [
            DialogSection(
                title="项目信息",
                step="1",
                tone="primary",
                rows=[
                    ("项目", f"{project['name']} / 用户：{selected_user}"),
                    ("口播稿", str(spoken_path)),
                    ("草稿名", self.account_var.get().strip() or "未填写"),
                    ("草稿输出", DEFAULT_JIANYING_DRAFT_ROOT),
                ],
            ),
            DialogSection(
                title="素材使用",
                step="2",
                tone="success",
                rows=[
                    ("使用包", f"{len(entries)} 条 manifest（商品 {entry_counts['product']}，过渡/引言 {entry_counts['transition']}，结尾 {entry_counts['closing']}）"),
                    ("商品示例", "；".join(product_names[:6]) if product_names else "无"),
                    ("引言视频", str(intro_video_path) if intro_video_path else "未选择，将使用 manifest 内的引言配音"),
                    ("缺失文件", f"音频 {len(missing_by_type['audio'])}，图片 {len(missing_by_type['image'])}，视频 {len(missing_by_type['video'])}"),
                ],
                items=preview_lines(asset_issue_items, limit=100) if asset_issue_items else [],
                helper="这里只显示缺配音、缺图片、缺视频或路径不存在的记录；正常素材路径不再展开。",
            ),
            DialogSection(
                title="检查结果",
                step="3",
                tone="warning" if (missing_manifest or manifest_error or missing_by_type['audio']) else "success",
                items=result_items,
                helper="" if result_items else "当前没有阻塞项，可以继续生成剪映草稿。",
            ),
            DialogSection(
                title="数据库资产总览",
                step="4",
                tone="info",
                rows=[
                    ("缺图片", str(len(issues["missing_image"]))),
                    ("缺视频", str(len(issues["missing_video"]))),
                    ("缺配音", str(len(issues["missing_voice"]))),
                    ("配音过期", str(len(issues["expired_voice"]))),
                ],
                helper="这里按当前项目数据库资产统计；本次剪映实际阻塞以 manifest 检查为准。缺视频不阻塞，会用商品图兜底。",
            ),
        ]
        can_continue = (
            not missing_manifest
            and not manifest_error
            and not missing_by_type["audio"]
            and has_bg
            and (intro_video_path is None or intro_video_path.exists())
        )
        return sections, can_continue

    def refresh(self) -> None:
        project = self.app.current_project()
        self.app.sync_project_selectors()
        if project:
            if self.loaded_project_id != project["id"]:
                self.spoken_md_var.set(safe_text(project.get("spoken_md_path")))
                self.loaded_project_id = project["id"]
        if self._is_voice_page():
            users = account_labels_for_voice_provider(self.repo.accounts(), self._selected_voice_provider())
        else:
            users = [a["label"] for a in self.repo.accounts()]
        account_input = getattr(self, "account_input", None)
        if account_input is not None:
            account_input.configure(values=users)
            if users and self.account_var.get() not in users:
                self.account_var.set(users[0])
            elif not users and self._is_voice_page():
                self.account_var.set("")
        if self._is_assemble_page():
            self._refresh_intro_choices(project)
            if users:
                self.asm_user_var.set(self.account_var.get())
            self._on_asm_user_changed()
        if self._is_jianying_page():
            if project and not self.spoken_md_var.get().strip():
                self.spoken_md_var.set(
                    safe_text(project.get("spoken_md_path"))
                    or str(default_spoken_markdown_path(project)).replace("/", "\\")
                )
            self._update_jianying_draft_name()
        if project and not self.spoken_md_var.get().strip():
            self.spoken_md_var.set(
                safe_text(project.get("spoken_md_path"))
                or str(default_spoken_markdown_path(project, self.account_var.get().strip())).replace("/", "\\")
            )
        if self._is_voice_page():
            self._update_voice_output_dir(force=True)

    def _select_project(self, _=None) -> None:
        v = self.project_var.get()
        if not v:
            return
        pid = self.app.project_id_for_selector_value(v)
        if pid is not None:
            self.app.set_current_project(pid)
