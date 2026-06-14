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
    COLOR_TEXT_DIM = "#B6C2D4"  # 次要文字（已提亮以加强深色背景上的对比）
    COLOR_TEXT_ACCENT = COLOR_PRIMARY  # 强调文字统一复用品牌粉，不再用第二个粉
    COLOR_BORDER = "#253248"
    COLOR_SUCCESS = "#43A877"  # 状态色整体降饱和，融进深色而不是跳出来
    COLOR_WARNING = "#CC9A4B"
    COLOR_ERROR = "#C96A60"
    COLOR_ERROR_HOVER = "#B25750"
    COLOR_INFO = "#5783AE"
    COLOR_ISSUE_BG = "#3A2020"  # 问题行背景
    COLOR_INPUT_BG = "#151E2D"
    COLOR_TABLE_HEADER = "#101827"
    COLOR_TABLE_ROW = "#121A28"
    COLOR_TABLE_ROW_ALT = "#151E2D"
    COLOR_SURFACE_SOFT = "#0F1724"
    COLOR_SUCCESS_BG = "#163627"
    COLOR_FIELD_EMPTY_BORDER = "#F39C12"
    COLOR_FIELD_EMPTY_TEXT = "#FFD38A"
    COLOR_FIELD_NORMAL_BORDER = "#384A66"  # 提亮：输入框填值后边框也清晰可见
    COLOR_LOG_BORDER = "#33445F"
    COLOR_LOG_SCROLLBAR = "#42516A"
    COLOR_LOG_SCROLLBAR_HOVER = "#5B6B87"
    COLOR_LOG_INFO = "#4B83F1"
    COLOR_LOG_ICON_TEXT = "#07111F"
    COLOR_LOG_TAG_BG = "#1A315A"
    COLOR_LOG_TAG_TEXT = "#6EA8FF"
    COLOR_LOG_DIVIDER = "#223047"
    COLOR_TABLE_SELECTED = "#233247"
    COLOR_TABLE_ACTIVE = "#162233"
    COLOR_ASSET_TABLE_ROW = "#131D2B"
    COLOR_ASSET_TABLE_ROW_ALT = "#162233"
    COLOR_ASSET_TABLE_HEADER = "#111927"
    COLOR_ASSET_TABLE_TEXT = "#E8EEF8"
    COLOR_ASSET_TABLE_HEADING_TEXT = "#D6DFEC"
    COLOR_ASSET_ISSUE_ROW = "#3A2426"
    COLOR_ASSET_ISSUE_ROW_ALT = "#43292C"
    COLOR_ASSET_ISSUE_TEXT = "#F7D7D9"
    COLOR_ASSET_VIDEO = "#8B5CF6"
    COLOR_TOAST_BG = "#121A28"

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
    FONT_DISPLAY = ("Microsoft YaHei", 26, "bold")  # 运行对话框 hero 大标题
    FONT_H1 = ("Microsoft YaHei", 20, "bold")
    FONT_H2 = ("Microsoft YaHei", 16, "bold")
    FONT_H3 = ("Microsoft YaHei", 14, "bold")
    FONT_STAT = ("Microsoft YaHei", 24, "bold")  # 统计卡大数字
    FONT_BODY = ("Microsoft YaHei", 13)
    FONT_SMALL = ("Microsoft YaHei", 12)
    FONT_LABEL_STRONG = ("Microsoft YaHei", 12, "bold")  # 小号粗标签（日志 tag、表头）
    FONT_NAV_GROUP = ("Microsoft YaHei", 12, "bold")
    FONT_NAV_ICON = ("Segoe MDL2 Assets", 15)
    FONT_ICON_LG = ("Segoe MDL2 Assets", 52)
    FONT_ICON_MD = ("Segoe MDL2 Assets", 20)
    FONT_ICON_SM = ("Segoe MDL2 Assets", 13)
    FONT_TABLE = ("Microsoft YaHei", 12)
    FONT_BUTTON = ("Microsoft YaHei", 13, "bold")

    # ── Segoe MDL2 图标 ──
    ICON_PROGRESS = "\uE9F5"
    ICON_LOG = "\uE9D9"
    ICON_SUCCESS = "\uE73E"
    ICON_ERROR = "\uE711"
    ICON_WARNING = "\uE7BA"
    ICON_INFO = "\uE946"
