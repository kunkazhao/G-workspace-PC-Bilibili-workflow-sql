from __future__ import annotations

"""全局样式配置 —— 所有 UI 视觉参数集中在此处，改一行全局生效。"""


class UIStyle:
    # ── 颜色系统 ──
    COLOR_PRIMARY = "#F64F86"  # B 站品牌粉
    COLOR_PRIMARY_HOVER = "#E8477B"
    COLOR_BG = "#090F1A"  # 深色背景（主窗口）
    COLOR_CARD_BG = "#121A28"  # 卡片/容器背景
    COLOR_SIDEBAR_BG = "#0B111D"
    COLOR_NAV_ACTIVE = "#1B2535"
    COLOR_NAV_HOVER = "#202B3D"
    COLOR_NAV_GROUP = "#7C879B"
    COLOR_NAV_ICON = "#718098"
    COLOR_TEXT_MAIN = "#F7FAFF"
    COLOR_TEXT_DIM = "#A8B3C5"
    COLOR_TEXT_ACCENT = "#FB7299"
    COLOR_BORDER = "#253248"
    COLOR_SUCCESS = "#2ECC71"
    COLOR_WARNING = "#F39C12"
    COLOR_ERROR = "#E74C3C"
    COLOR_INFO = "#3498DB"
    COLOR_ISSUE_BG = "#3A2020"  # 问题行背景
    COLOR_INPUT_BG = "#151E2D"
    COLOR_TABLE_HEADER = "#101827"
    COLOR_TABLE_ROW = "#121A28"
    COLOR_TABLE_ROW_ALT = "#151E2D"
    COLOR_SURFACE_SOFT = "#0F1724"
    COLOR_SUCCESS_BG = "#163627"

    # ── 几何参数 ──
    RADIUS_LG = 8
    RADIUS_MD = 6
    RADIUS_SM = 6
    PAD_XL = 20
    PAD_LG = 16
    PAD_MD = 12
    PAD_SM = 8
    PAD_XS = 4
    SIDEBAR_WIDTH = 202
    INPUT_HEIGHT = 36
    BUTTON_HEIGHT = 36

    # ── 字体规范（标准 Tkinter 元组格式：(family, size, style)）──
    FONT_H1 = ("Microsoft YaHei", 20, "bold")
    FONT_H2 = ("Microsoft YaHei", 16, "bold")
    FONT_H3 = ("Microsoft YaHei", 14, "bold")
    FONT_BODY = ("Microsoft YaHei", 13)
    FONT_SMALL = ("Microsoft YaHei", 12)
    FONT_NAV_GROUP = ("Microsoft YaHei", 12, "bold")
    FONT_NAV_ICON = ("Segoe MDL2 Assets", 15)
    FONT_TABLE = ("Microsoft YaHei", 12)
    FONT_BUTTON = ("Microsoft YaHei", 13, "bold")
