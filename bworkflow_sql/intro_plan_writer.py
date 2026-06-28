from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .outline_service import OutlineService
from .settings import CUTME_ROOT, INTERNAL_WORKSPACE_ROOT
from .utils import safe_text


DEFAULT_INTRO_TEMPLATE_ID = "pain_avoidance_priority_v1"


@dataclass(frozen=True)
class IntroPlanWriteResult:
    intro_plan_path: Path
    slots_path: Path
    markdown_path: Path
    label: str
    full_script: str
    synced: bool
    sync_result: dict[str, Any] | None = None


def render_intro_plan_from_slots(
    *,
    slots: dict[str, Any],
    template_id: str = DEFAULT_INTRO_TEMPLATE_ID,
) -> dict[str, Any]:
    _ensure_cutme_import_path()
    from cutme.intro_script import (
        load_intro_template,
        render_intro_script,
        rendered_intro_to_plan_fragment,
    )

    template = load_intro_template(template_id)
    rendered = render_intro_script(template, {key: str(value) for key, value in slots.items()})
    return rendered_intro_to_plan_fragment(rendered)


def render_intro_plan_file(
    *,
    slots_path: str | Path,
    output_path: str | Path,
    template_id: str = DEFAULT_INTRO_TEMPLATE_ID,
) -> dict[str, Any]:
    source = Path(slots_path)
    slots = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(slots, dict):
        raise ValueError("引言槽位文件必须是 JSON 对象")
    plan = render_intro_plan_from_slots(slots=slots, template_id=template_id)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def write_intro_plan_for_project(
    *,
    db: Database,
    project_id: int,
    slots_path: str | Path,
    template_id: str = DEFAULT_INTRO_TEMPLATE_ID,
    label: str = "引言1",
    markdown_path: str | Path | None = None,
    sync: bool = False,
) -> IntroPlanWriteResult:
    from .sync_service import SyncService

    project = db.fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
    if not project:
        raise ValueError(f"项目不存在：{project_id}")

    workspace = intro_authoring_workspace(project_id)
    safe_label = _safe_path_part(label) or "intro"
    plan_path = workspace / f"source-intro-plan-{safe_label}.json"
    saved_slots_path = workspace / f"intro-slots-{safe_label}.json"

    source_slots = Path(slots_path)
    if not source_slots.is_file():
        raise FileNotFoundError(f"引言槽位文件不存在：{source_slots}")
    saved_slots_path.parent.mkdir(parents=True, exist_ok=True)
    if source_slots.resolve() != saved_slots_path.resolve():
        shutil.copyfile(source_slots, saved_slots_path)

    plan = render_intro_plan_file(
        slots_path=saved_slots_path,
        output_path=plan_path,
        template_id=template_id,
    )
    target_md = Path(markdown_path) if markdown_path else _project_markdown_path(db, project_id)
    upsert_intro_markdown_block(target_md, label=label, body=safe_text(plan.get("full_script")))
    db.execute("UPDATE projects SET md_path=?, updated_at=datetime('now') WHERE id=?", (str(target_md), project_id))

    sync_result = None
    if sync:
        sync_result = SyncService(db).sync_markdown(project_id)

    return IntroPlanWriteResult(
        intro_plan_path=plan_path,
        slots_path=saved_slots_path,
        markdown_path=target_md,
        label=label,
        full_script=safe_text(plan.get("full_script")),
        synced=sync,
        sync_result=sync_result,
    )


def intro_authoring_workspace(project_id: int) -> Path:
    return INTERNAL_WORKSPACE_ROOT / f"project-{int(project_id)}" / "intro"


def upsert_intro_markdown_block(markdown_path: str | Path, *, label: str, body: str) -> None:
    path = Path(markdown_path)
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = original.splitlines()
    if not lines:
        lines = ["## 引言文案", ""]

    section_start = _find_section(lines, "引言文案")
    if section_start < 0:
        insert_at = _frontmatter_end(lines)
        prefix = lines[:insert_at]
        suffix = lines[insert_at:]
        if prefix and prefix[-1].strip():
            prefix.append("")
        lines = prefix + ["## 引言文案", ""] + suffix
        section_start = len(prefix)

    section_end = _find_next_section(lines, section_start + 1)
    if section_end < 0:
        section_end = len(lines)

    heading_start = _find_intro_heading(lines, section_start + 1, section_end, label)
    body_lines = [body.strip(), ""]
    if heading_start >= 0:
        replace_start = heading_start + 1
        replace_end = _find_next_intro_heading_or_section(lines, replace_start)
        lines[replace_start:replace_end] = body_lines
    else:
        insertion = [f"### {label}", ""] + body_lines
        if section_end > 0 and lines[section_end - 1].strip():
            insertion.insert(0, "")
        lines[section_end:section_end] = insertion

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_normalize_blank_lines(lines)).rstrip() + "\n", encoding="utf-8")


def _project_markdown_path(db: Database, project_id: int) -> Path:
    row = db.fetchone("SELECT md_path FROM projects WHERE id=?", (project_id,))
    md_path = safe_text(row["md_path"] if row else "")
    if md_path:
        return Path(md_path)
    return OutlineService(db).default_markdown_path(project_id)


def _find_section(lines: list[str], title: str) -> int:
    wanted = f"## {title}"
    for index, raw in enumerate(lines):
        if raw.strip() == wanted:
            return index
    return -1


def _find_next_section(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        if lines[index].strip().startswith("## "):
            return index
    return -1


def _find_intro_heading(lines: list[str], start: int, end: int, label: str) -> int:
    wanted = f"### {label}"
    for index in range(start, end):
        if lines[index].strip() == wanted:
            return index
    return -1


def _find_next_intro_heading_or_section(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("### ") or stripped.startswith("## "):
            return index
    return len(lines)


def _frontmatter_end(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return index + 1
    return 0


def _normalize_blank_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    blank_count = 0
    for raw in lines:
        line = raw.rstrip()
        if line.strip():
            blank_count = 0
            output.append(line)
            continue
        blank_count += 1
        if blank_count <= 1:
            output.append("")
    return output


def _ensure_cutme_import_path() -> None:
    root = str(CUTME_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _safe_path_part(value: str) -> str:
    text = safe_text(value)
    for char in '<>:"/\\|?*':
        text = text.replace(char, "_")
    return text.strip(" .")
