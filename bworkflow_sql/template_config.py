from __future__ import annotations

from typing import Any

# 每个模板的视频展示区域坐标（相对于 1920*1080 画布）
# 数据来源：G:\workspace\PC-Bilibili-workflow\data\display_video_templates.json
TEMPLATE_COORDS: dict[str, dict[str, Any]] = {
    "小博-模板1": {"x": 850, "y": 95, "width": 980, "height": 620, "round_corner": 25},
    "小博-模板2": {"x": 1015, "y": 154, "width": 680, "height": 520},
    "小博-模板3": {"x": 1015, "y": 154, "width": 680, "height": 520},
    "小燃-模板1": {"x": 757, "y": 334, "width": 970, "height": 590},
    "小燃-模板2": {"x": 50, "y": 322, "width": 1004, "height": 588},
    "小歪-模板1": {"x": 48, "y": 316, "width": 984, "height": 600},
    "小歪-模板2": {"x": -56, "y": 420, "width": 1132, "height": 576},
}

# 每个用户对应的可用模板列表
USER_TEMPLATES: dict[str, list[str]] = {
    "小博": ["小博-模板1", "小博-模板2", "小博-模板3"],
    "小燃": ["小燃-模板1", "小燃-模板2"],
    "小歪": ["小歪-模板1", "小歪-模板2"],
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


def user_for_template(template_name: str) -> str:
    """根据模板名反查所属用户。"""
    for user, templates in USER_TEMPLATES.items():
        if template_name in templates:
            return user
    return ""
