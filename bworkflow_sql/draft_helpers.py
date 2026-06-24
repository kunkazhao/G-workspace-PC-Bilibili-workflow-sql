from __future__ import annotations

import json
from typing import Any

from .utils import safe_text


def parse_json_object(text: str) -> Any:
    raw = safe_text(text).strip()
    if not raw:
        return None
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def format_duration_cn(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def format_jianying_run_stdout(stdout: str) -> str:
    payload = parse_json_object(stdout)
    if not isinstance(payload, dict):
        return stdout

    status = safe_text(payload.get("status"))
    if status == "failed":
        error = safe_text(payload.get("error")) or "未知错误"
        return f"生成失败：{error}\n"

    lines: list[str] = []
    draft_name = safe_text(payload.get("draft_name"))
    draft_dir = safe_text(payload.get("draft_dir"))
    if draft_name:
        lines.append(f"草稿名称：{draft_name}")
    if draft_dir:
        lines.append(f"草稿已写入：{draft_dir}")

    total_items = int(payload.get("total_items") or 0)
    product_items = int(payload.get("product_items") or 0)
    if total_items:
        lines.append(f"本次共拼接 {total_items} 段素材，其中商品推荐 {product_items} 段。")

    total_duration = float(payload.get("total_duration_sec") or 0)
    if total_duration > 0:
        lines.append(f"草稿总时长约 {format_duration_cn(total_duration)}。")

    if payload.get("has_intro_video"):
        intro_duration = float(payload.get("intro_duration_sec") or 0)
        suffix = f"，时长约 {format_duration_cn(intro_duration)}" if intro_duration > 0 else ""
        lines.append(f"已使用引言成片视频{suffix}。")

    display_video_segments = int(payload.get("display_video_segments") or 0)
    if display_video_segments:
        lines.append(f"已插入 {display_video_segments} 段商品展示视频。")

    price_transition_title_segments = int(payload.get("price_transition_title_segments") or 0)
    if price_transition_title_segments:
        lines.append(f"已插入 {price_transition_title_segments} 段价格过渡标题。")

    subtitle_segments = int(payload.get("subtitle_segments") or 0)
    if subtitle_segments:
        lines.append(f"已生成 {subtitle_segments} 段字幕。")

    image_fallback = payload.get("image_fallback")
    if isinstance(image_fallback, dict):
        resolved_count = int(image_fallback.get("resolved_count") or 0)
        missing_uids = [safe_text(item) for item in image_fallback.get("missing_uids") or [] if safe_text(item)]
        if resolved_count:
            lines.append(f"有 {resolved_count} 个商品图片已从图片索引自动补齐。")
        if missing_uids:
            lines.append(f"仍有 {len(missing_uids)} 个商品没有找到可用图片：{'、'.join(missing_uids[:8])}")

    skipped_entries = payload.get("skipped_entries")
    if isinstance(skipped_entries, list) and skipped_entries:
        lines.append(f"有 {len(skipped_entries)} 个条目因缺少素材被跳过，请检查口播稿清单。")

    skipped_display_videos = payload.get("skipped_display_videos")
    if isinstance(skipped_display_videos, list) and skipped_display_videos:
        lines.append(f"有 {len(skipped_display_videos)} 个商品展示视频因文件缺失或格式不支持被跳过：")
        for item in skipped_display_videos[:8]:
            name = safe_text(item.get("product_name")) or safe_text(item.get("product_uid")) or f"条目 {item.get('index')}"
            reason = safe_text(item.get("reason"))
            lines.append(f"  - {name}（{reason}）")
        if len(skipped_display_videos) > 8:
            lines.append(f"  ……等共 {len(skipped_display_videos)} 个")

    missing_subtitle_texts = payload.get("missing_subtitle_texts")
    if isinstance(missing_subtitle_texts, list) and missing_subtitle_texts:
        lines.append(f"有 {len(missing_subtitle_texts)} 段音频缺少字幕文本，已跳过字幕生成。")

    if not lines:
        return stdout
    lines.append("剪映草稿生成完成，可以在剪映草稿列表中打开。")
    return "\n".join(lines) + "\n"
