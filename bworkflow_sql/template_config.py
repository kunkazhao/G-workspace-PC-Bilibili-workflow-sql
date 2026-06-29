from __future__ import annotations

from typing import Any

# 每个模板的视频展示区域坐标（相对于 1920*1080 画布）
# 数据来源：G:\workspace\PC-Bilibili-workflow\data\display_video_templates.json
#
# ── 剪映 UI 参数 ↔ 本文件坐标的转换公式 ──
# 剪映X = (center_x - 960) × 2      center_x = 960 + 剪映X / 2
# 剪映Y = (540 - center_y) × 2      center_y = 540 - 剪映Y / 2
# 剪映缩放% = display_scale × 100
# 其中 center_x = x + width/2, center_y = y + height/2
# 注意：乘/除 2，不是乘/除 960/540，之前在这里踩过坑。
TEMPLATE_COORDS: dict[str, dict[str, Any]] = {
    "小博-模板1": {"x": 850, "y": 95, "width": 980, "height": 620},
    "小博-模板2": {"x": 1015, "y": 154, "width": 680, "height": 520, "display_scale": 0.52},
    "小博-模板3": {"x": 1015, "y": 154, "width": 680, "height": 520, "display_scale": 0.52},
    "小燃-模板1": {"x": -830, "y": -77, "width": 970, "height": 590, "coordinate_mode": "clip_transform_pixels"},
    "小燃-模板2": {"x": 50, "y": 322, "width": 1004, "height": 588},
    "小歪-模板1": {"x": -855, "y": -22, "width": 960, "height": 540, "coordinate_mode": "clip_transform_pixels"},
    "小歪-模板2": {"x": -29, "y": 202, "width": 1132, "height": 676, "display_scale": 0.53},
    "知了-模板1": {"x": 67, "y": 185, "width": 990, "height": 576},
    "荣荣-模板1": {"x": 115, "y": 200, "width": 941, "height": 554},
    "荣荣-模板2": {"x": 44, "y": 172, "width": 851, "height": 436, "display_scale": 0.42},
}

# 每个用户对应的可用模板列表
USER_TEMPLATES: dict[str, list[str]] = {
    "小博": ["小博-模板1", "小博-模板2", "小博-模板3"],
    "小燃": ["小燃-模板1", "小燃-模板2"],
    "小歪": ["小歪-模板1", "小歪-模板2"],
    "知了": ["知了-模板1"],
    "荣荣": ["荣荣-模板1", "荣荣-模板2"],
}


def get_template_slot(template_name: str) -> dict[str, Any]:
    """根据模板名称查询视频展示区域坐标。"""
    coords = TEMPLATE_COORDS.get(template_name)
    if coords is None:
        raise ValueError(f"未知模板：{template_name}")
    return dict(coords)


def available_templates(user_label: str) -> list[str]:
    """获取某个用户可用的模板列表。"""
    return list(USER_TEMPLATES.get(user_label, []))


def image_set_for_template(template_name: str) -> str:
    """根据模板显示名推导素材目录关键字。"""
    if not template_name:
        return ""
    if "-" in template_name:
        return template_name.split("-", 1)[1]
    return template_name


def user_for_template(template_name: str) -> str:
    """根据模板名反查所属用户。"""
    for user, templates in USER_TEMPLATES.items():
        if template_name in templates:
            return user
    return ""
