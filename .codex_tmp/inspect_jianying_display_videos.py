from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def material_path_map(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    materials = payload.get("materials")
    if not isinstance(materials, dict):
        return result
    for group in materials.values():
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            material_id = str(item.get("id") or item.get("material_id") or "")
            path = str(item.get("path") or item.get("local_material_id") or item.get("name") or "")
            if material_id and path:
                result[material_id] = path
    return result


def summarize_draft(draft_dir: Path) -> dict[str, Any]:
    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        timeline_files = sorted((draft_dir / "Timelines").glob("*/draft_content.json"))
        content_path = timeline_files[0] if timeline_files else content_path
    try:
        payload = read_json(content_path)
    except Exception as exc:
        return {
            "draft": str(draft_dir),
            "content_path": str(content_path),
            "error": str(exc),
            "video_nodes": [],
        }
    paths = material_path_map(payload)
    display_like: list[dict[str, Any]] = []
    for node in iter_dicts(payload):
        material_id = str(node.get("material_id") or "")
        path = paths.get(material_id, "")
        if not path.lower().endswith((".mov", ".mp4", ".mkv", ".avi")):
            continue
        display_like.append(
            {
                "material_id": material_id,
                "path": path,
                "alpha": node.get("alpha"),
                "blend": node.get("blend"),
                "enable_video_mask": node.get("enable_video_mask"),
                "enable_adjust_mask": node.get("enable_adjust_mask"),
                "enable_mask_shadow": node.get("enable_mask_shadow"),
                "enable_mask_stroke": node.get("enable_mask_stroke"),
                "extra_material_refs": node.get("extra_material_refs"),
                "transform": node.get("transform"),
                "source_timerange": node.get("source_timerange"),
                "target_timerange": node.get("target_timerange"),
                "visible": node.get("visible"),
            }
        )
    return {
        "draft": str(draft_dir),
        "content_path": str(content_path),
        "video_nodes": display_like,
    }


def main() -> None:
    drafts = sorted([p for p in DRAFT_ROOT.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    summaries = [summarize_draft(draft) for draft in drafts]
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
