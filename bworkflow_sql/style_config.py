from __future__ import annotations

"""全局样式配置 —— 所有 UI 视觉参数集中在此处，改一行全局生效。"""


class UIStyle:
    # ── 颜色系统 ──
    COLOR_PRIMARY = "#FB7299"  # B 站品牌粉
    COLOR_PRIMARY_HOVER = "#E06584"
    COLOR_BG = "#1A1A1B"  # 深色背景（主窗口）
    COLOR_CARD_BG = "#2B2B2C"  # 卡片/容器背景
    COLOR_SIDEBAR_BG = "#1E1E1F"
    COLOR_NAV_ACTIVE = "#2F2F30"
    COLOR_NAV_HOVER = "#38383A"
    COLOR_TEXT_MAIN = "#FFFFFF"
    COLOR_TEXT_DIM = "#A0A0A0"
    COLOR_TEXT_ACCENT = "#FB7299"
    COLOR_BORDER = "#3A3A3C"
    COLOR_SUCCESS = "#2ECC71"
    COLOR_WARNING = "#F39C12"
    COLOR_ERROR = "#E74C3C"
    COLOR_INFO = "#3498DB"
    COLOR_ISSUE_BG = "#3A2020"  # 问题行背景
    COLOR_INPUT_BG = "#333334"
    COLOR_TABLE_HEADER = "#252526"
    COLOR_TABLE_ROW = "#2B2B2C"
    COLOR_TABLE_ROW_ALT = "#2E2E2F"

    # ── 几何参数 ──
    RADIUS_LG = 12
    RADIUS_MD = 8
    RADIUS_SM = 6
    PAD_XL = 20
    PAD_LG = 16
    PAD_MD = 12
    PAD_SM = 8
    PAD_XS = 4
    SIDEBAR_WIDTH = 220
    INPUT_HEIGHT = 36
    BUTTON_HEIGHT = 36

    # ── 字体规范（标准 Tkinter 元组格式：(family, size, style)）──
    FONT_H1 = ("Microsoft YaHei", 20, "bold")
    FONT_H2 = ("Microsoft YaHei", 16, "bold")
    FONT_H3 = ("Microsoft YaHei", 14, "bold")
    FONT_BODY = ("Microsoft YaHei", 13)
    FONT_SMALL = ("Microsoft YaHei", 12)
    FONT_TABLE = ("Microsoft YaHei", 12)
    FONT_BUTTON = ("Microsoft YaHei", 13, "bold")
