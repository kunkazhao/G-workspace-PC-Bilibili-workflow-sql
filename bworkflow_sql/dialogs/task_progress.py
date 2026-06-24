from __future__ import annotations

import re
import threading
from datetime import datetime
import tkinter as tk

import customtkinter as ctk

from ..components import GhostButton
from ..style_config import UIStyle
from ..ui_helpers import _center_dialog, _restore_window


class TaskProgressDialog(ctk.CTkToplevel):
    def __init__(self, parent: tk.Widget, title: str, message: str):
        super().__init__(parent)
        self._parent_toplevel = parent.winfo_toplevel()
        self.title(title)
        self.geometry("1120x760")
        self.minsize(900, 620)
        # 不使用 transient()：该绑定会导致对话框关闭时 Windows 将主窗口一起降到
        # Z 序底部，且 zoomed 状态下任务栏图标点击无法恢复主窗口。
        self.configure(fg_color=UIStyle.COLOR_BG)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.hero_title_var = ctk.StringVar(value=title)
        self.status_var = ctk.StringVar(value=message)
        self.detail_var = ctk.StringVar(value="")
        self._log_count = 0
        self.cancel_event = threading.Event()

        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.grid(row=0, column=0, sticky="nsew", padx=UIStyle.PAD_XL, pady=(UIStyle.PAD_XL, UIStyle.PAD_MD))
        shell.rowconfigure(1, weight=1)
        shell.columnconfigure(0, weight=1)

        hero = ctk.CTkFrame(shell, fg_color="transparent")
        hero.grid(row=0, column=0, sticky="ew", pady=(UIStyle.PAD_MD, UIStyle.PAD_XL))
        self.icon_label = ctk.CTkLabel(
            hero,
            text=UIStyle.ICON_PROGRESS,
            font=UIStyle.FONT_ICON_LG,
            text_color=UIStyle.COLOR_INFO,
        )
        self.icon_label.pack(pady=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(hero, textvariable=self.hero_title_var, font=UIStyle.FONT_DISPLAY).pack()
        ctk.CTkLabel(
            hero,
            textvariable=self.status_var,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM,
            justify="center",
            wraplength=860,
        ).pack(pady=(UIStyle.PAD_SM, UIStyle.PAD_XS))
        self.detail_label = ctk.CTkLabel(
            hero,
            textvariable=self.detail_var,
            font=UIStyle.FONT_H3,
            text_color=UIStyle.COLOR_TEXT_MAIN,
        )
        self.detail_label.pack()

        self.progress = ctk.CTkProgressBar(hero, mode="indeterminate", height=12, corner_radius=999)
        self.progress.pack(fill="x", padx=130, pady=(UIStyle.PAD_XL, 0))
        self.progress.start()

        log_card = ctk.CTkFrame(
            shell,
            fg_color=UIStyle.COLOR_CARD_BG,
            corner_radius=UIStyle.RADIUS_LG,
            border_width=1,
            border_color=UIStyle.COLOR_LOG_BORDER,
        )
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=UIStyle.PAD_LG, pady=(UIStyle.PAD_LG, UIStyle.PAD_MD))
        ctk.CTkLabel(
            log_header,
            text=UIStyle.ICON_LOG,
            font=UIStyle.FONT_ICON_MD,
            text_color=UIStyle.COLOR_INFO,
        ).pack(side="left", padx=(0, UIStyle.PAD_SM))
        ctk.CTkLabel(log_header, text="执行日志", font=UIStyle.FONT_H2, text_color=UIStyle.COLOR_TEXT_MAIN).pack(side="left")

        self.log_scroll = ctk.CTkScrollableFrame(
            log_card,
            fg_color="transparent",
            scrollbar_button_color=UIStyle.COLOR_LOG_SCROLLBAR,
            scrollbar_button_hover_color=UIStyle.COLOR_LOG_SCROLLBAR_HOVER,
        )
        self.log_scroll.grid(row=1, column=0, sticky="nsew", padx=UIStyle.PAD_LG, pady=(0, UIStyle.PAD_SM))
        self.log_scroll.grid_columnconfigure(0, weight=1)

        buttons = ctk.CTkFrame(shell, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", pady=(UIStyle.PAD_LG, 0))
        buttons.columnconfigure(0, weight=1)
        self.cancel_button = GhostButton(buttons, text="取消", command=self._request_cancel)
        self.cancel_button.grid(row=0, column=1, padx=(0, UIStyle.PAD_MD))
        self.close_button = GhostButton(buttons, text="关闭", command=self.destroy)
        self.close_button.grid(row=0, column=2)
        self.close_button.configure(state="disabled")
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.lift()
        self.focus_set()
        _center_dialog(self)

    def append(self, text: str) -> None:
        if not self.winfo_exists():
            return
        for value in text.splitlines():
            value = value.strip()
            if not value:
                continue
            self._append_log_row(value)

    def _append_log_row(self, value: str) -> None:
        self._log_count += 1
        kind, tag, message = self._parse_log_line(value)
        color = {
            "success": UIStyle.COLOR_SUCCESS,
            "error": UIStyle.COLOR_ERROR,
            "warning": UIStyle.COLOR_WARNING,
            "info": UIStyle.COLOR_LOG_INFO,
        }.get(kind, UIStyle.COLOR_LOG_INFO)
        icon = {
            "success": UIStyle.ICON_SUCCESS,
            "error": UIStyle.ICON_ERROR,
            "warning": UIStyle.ICON_WARNING,
            "info": UIStyle.ICON_INFO,
        }.get(kind, UIStyle.ICON_INFO)

        row = ctk.CTkFrame(self.log_scroll, fg_color="transparent")
        row.grid(row=self._log_count * 2, column=0, sticky="ew", padx=0, pady=0)
        row.grid_columnconfigure(3, weight=1)

        icon_label = ctk.CTkLabel(
            row,
            text=icon,
            width=24,
            height=24,
            corner_radius=999,
            fg_color=color,
            text_color=UIStyle.COLOR_LOG_ICON_TEXT,
            font=UIStyle.FONT_ICON_SM,
        )
        icon_label.grid(row=0, column=0, sticky="nw", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_SM)

        ctk.CTkLabel(
            row,
            text=datetime.now().strftime("%H:%M:%S"),
            width=78,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_DIM,
            anchor="w",
        ).grid(row=0, column=1, sticky="nw", padx=(0, UIStyle.PAD_MD), pady=UIStyle.PAD_SM)

        ctk.CTkLabel(
            row,
            text=tag,
            width=98,
            height=26,
            corner_radius=UIStyle.RADIUS_MD,
            fg_color=UIStyle.COLOR_LOG_TAG_BG,
            text_color=UIStyle.COLOR_LOG_TAG_TEXT,
            font=UIStyle.FONT_LABEL_STRONG,
        ).grid(row=0, column=2, sticky="nw", padx=(0, UIStyle.PAD_MD), pady=(UIStyle.PAD_SM - 1, UIStyle.PAD_SM))

        ctk.CTkLabel(
            row,
            text=message or value,
            font=UIStyle.FONT_BODY,
            text_color=UIStyle.COLOR_TEXT_MAIN,
            justify="left",
            anchor="w",
            wraplength=720,
        ).grid(row=0, column=3, sticky="ew", pady=UIStyle.PAD_SM)

        line = ctk.CTkFrame(self.log_scroll, fg_color=UIStyle.COLOR_LOG_DIVIDER, height=1)
        line.grid(row=self._log_count * 2 + 1, column=0, sticky="ew")
        canvas = getattr(self.log_scroll, "_parent_canvas", None)
        if canvas is not None:
            canvas.yview_moveto(1.0)

    def _parse_log_line(self, value: str) -> tuple[str, str, str]:
        match = re.match(r"^\[(?P<tag>[^\]]+)\]\s*(?P<body>.*)$", value)
        if match:
            tag = match.group("tag").strip()
            body = match.group("body").strip()
        elif "：" in value:
            tag, body = value.split("：", 1)
            tag = tag.strip()[:8] or "日志"
            body = body.strip()
        else:
            tag, body = "日志", value

        if tag.startswith("成功") or "完成" in value or "已就绪" in value:
            kind = "success"
        elif tag.startswith("失败") or "失败" in value or "错误" in value:
            kind = "error"
        elif "未启动" in value or "跳过" in value or "警告" in value or "退出码：1" in value:
            kind = "warning"
        else:
            kind = "info"
        return kind, tag, body

    def _request_cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled", text="正在取消…")
        self.status_var.set("正在等待当前条目完成后取消…")
        self.append("[取消] 用户已请求取消，将在当前条目完成后停止。")

    def finish(self, message: str, *, kind: str = "success", headline: str | None = None, detail: str = "") -> None:
        if not self.winfo_exists():
            return
        self.progress.stop()
        self.progress.pack_forget()
        palette = {
            "success": (UIStyle.COLOR_SUCCESS, UIStyle.ICON_SUCCESS),
            "warning": (UIStyle.COLOR_WARNING, UIStyle.ICON_WARNING),
            "error": (UIStyle.COLOR_ERROR, UIStyle.ICON_ERROR),
            "info": (UIStyle.COLOR_INFO, UIStyle.ICON_INFO),
        }
        color, icon = palette.get(kind, palette["success"])
        self.icon_label.configure(text=icon, text_color=color)
        if headline:
            self.hero_title_var.set(headline)
        self.status_var.set(message)
        self.detail_var.set(detail)
        self.cancel_button.grid_remove()
        self.close_button.configure(state="normal", command=self._close_and_restore_parent)
        self.protocol("WM_DELETE_WINDOW", self._close_and_restore_parent)
        self.close_button.focus_set()

    def _close_and_restore_parent(self) -> None:
        """关闭进度对话框并将焦点归还主窗口。"""
        try:
            parent = self._parent_toplevel
            self.destroy()
            # 延迟一帧让 Tk 完成窗口销毁后再恢复主窗口
            parent.after(50, lambda: _restore_window(parent))
        except Exception:
            try:
                self.destroy()
            except Exception:
                pass
