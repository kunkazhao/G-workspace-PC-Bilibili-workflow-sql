from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .settings import DEFAULT_INTRO_ASSET_ROOT
from .template_config import available_templates, image_set_for_template
from .utils import safe_text


def build_media_workspace_plan(
    project: dict[str, Any],
    accounts: Iterable[dict[str, Any]],
    *,
    intro_root: str | Path = DEFAULT_INTRO_ASSET_ROOT,
    account_filter: str = "",
    template_overrides: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    category = safe_text(project.get("name"))
    if not category:
        raise ValueError("项目缺少品类名（name），无法建立媒体工作区")
    image_root = safe_text(project.get("image_root"))
    voice_root = safe_text(project.get("voice_root"))
    video_root = safe_text(project.get("video_root"))
    selected = [
        row for row in accounts
        if int(row.get("enabled", 0)) == 1
        and (not account_filter or safe_text(row.get("label")) == account_filter)
    ]
    plan: list[dict[str, str]] = []
    if video_root:
        plan.append({"path": str(Path(video_root) / category), "kind": "roll_b", "account": "", "template": ""})
    plan.append({"path": str(Path(intro_root) / category), "kind": "intro_assets", "account": "", "template": ""})
    overrides = [safe_text(value) for value in (template_overrides or []) if safe_text(value)]
    for account in selected:
        label = safe_text(account.get("label"))
        if voice_root:
            plan.append({"path": str(Path(voice_root) / category / label), "kind": "voice", "account": label, "template": ""})
        template_dirs = overrides or [image_set_for_template(value) for value in available_templates(label)]
        for template_dir in dict.fromkeys(value for value in template_dirs if value):
            if image_root:
                plan.append({"path": str(Path(image_root) / category / label / template_dir), "kind": "product_images", "account": label, "template": template_dir})
    return plan


def ensure_media_workspace(plan: Iterable[dict[str, str]]) -> dict[str, Any]:
    created: list[dict[str, str]] = []
    existed: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    for entry in plan:
        path = Path(entry["path"])
        try:
            if path.exists():
                existed.append(dict(entry))
            else:
                path.mkdir(parents=True, exist_ok=True)
                created.append(dict(entry))
        except OSError as error:
            failed.append({**entry, "error": str(error)})
    return {"workspace_ready": not failed, "created": created, "existed": existed, "failed": failed}
