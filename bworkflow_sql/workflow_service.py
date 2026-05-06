from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .db import Database
from .legacy_bridge import legacy_script_path
from .repositories import Repository
from .settings import (
    B_WORKFLOW_SKILL_SCRIPTS,
    DEFAULT_JIANYING_DRAFT_ROOT,
    DEFAULT_OUTPUT_ROOT,
    INTERNAL_WORKSPACE_ROOT,
    PEIYINDAN_SKILL_SCRIPTS,
)
from .utils import safe_text


class WorkflowService:
    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)

    def export_project_markdown(self, project_id: int, target_path: str | Path | None = None) -> Path:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        products = self.repo.products(project_id, include_removed=False)
        blocks = self.repo.script_blocks(project_id)
        assets = self.repo.asset_bindings(project_id)
        target = Path(target_path) if target_path else self._internal_project_dir(project_id) / "project-export.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        by_type: dict[str, list[dict[str, Any]]] = {"intro": [], "product": [], "price_transition": []}
        for block in blocks:
            by_type.setdefault(block["script_type"], []).append(block)
        asset_paths: dict[tuple[str, str], str] = {}
        for asset in assets:
            if asset["status"] != "ready":
                continue
            key = (asset["uid"], asset["asset_type"])
            asset_paths.setdefault(key, safe_text(asset.get("path")))
        lines: list[str] = [f"# {project['name']}", ""]
        lines += ["## 引言文案", ""]
        for block in by_type.get("intro", []):
            lines += [f"### {block['block_label']}", block["body"], ""]
        lines += ["## 商品文案", ""]
        product_blocks: dict[str, list[dict[str, Any]]] = {}
        for block in by_type.get("product", []):
            product_blocks.setdefault(block["owner_uid"], []).append(block)
        for product in products:
            lines += [f"### {product['title']}-{product['uid']}-{product['price_label']}", ""]
            for block in product_blocks.get(product["uid"], []):
                lines += [f"#### {block['block_label']}", block["body"], ""]
            lines += [
                f"图片：{asset_paths.get((product['uid'], 'image'), '')}",
                f"视频：{asset_paths.get((product['uid'], 'video'), '')}",
                "",
            ]
        lines += ["## 价格过渡文案", ""]
        price_groups: dict[str, list[dict[str, Any]]] = {}
        for block in by_type.get("price_transition", []):
            price_groups.setdefault(block["price_range_label"], []).append(block)
        for label, group in price_groups.items():
            lines += [f"### {label}", ""]
            for block in group:
                lines += [f"#### {block['block_label']}", block["body"], ""]
        lines += ["## 商品顺序", ""]
        for index, product in enumerate(products, start=1):
            lines.append(f"{index}. {product['uid']} {product['title']}")
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return target

    def build_voice_command(self, project_id: int, account_label: str = "", uids: list[str] | None = None) -> list[str]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        markdown_path = self.export_project_markdown(project_id)
        account = self._resolve_account(account_label)
        out_dir = Path(safe_text(project.get("voice_root")) or DEFAULT_OUTPUT_ROOT) / safe_text(account.get("label") or account_label or "voice")
        registry_path = out_dir / "audio_segment_registry.json"
        cmd = [
            "python",
            str(PEIYINDAN_SKILL_SCRIPTS / "run_peiyindan.py"),
            "--markdown-path",
            str(markdown_path),
            "--output-dir",
            str(out_dir),
            "--registry-path",
            str(registry_path),
        ]
        if account:
            if safe_text(account.get("account_id")):
                cmd += ["--account-id", safe_text(account.get("account_id"))]
            if safe_text(account.get("voice_id")):
                cmd += ["--voice-id", safe_text(account.get("voice_id"))]
            if safe_text(account.get("voice_name")):
                cmd += ["--voice-name", safe_text(account.get("voice_name"))]
        if uids:
            cmd += ["--uids", ",".join(uids)]
        return cmd

    def build_assembly_command(
        self,
        project_id: int,
        *,
        mode: str = "standard",
        top_uids: list[str] | None = None,
        account_label: str = "",
        intro_index: int = 1,
        product_uids: list[str] | None = None,
        output_markdown_path: str | Path | None = None,
    ) -> list[str]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        markdown_path = self.export_project_markdown(project_id)
        output_markdown = self._spoken_markdown_path(project, output_markdown_path)
        manifest_output = manifest_path_for_markdown(output_markdown)
        account = self._resolve_account(account_label)
        registry_dir = Path(safe_text(project.get("voice_root")) or DEFAULT_OUTPUT_ROOT) / safe_text(account.get("label") or account_label or "voice")
        cmd = [
            "python",
            str(B_WORKFLOW_SKILL_SCRIPTS / "generate_spoken_script.py"),
            "--source-markdown",
            str(markdown_path),
            "--intro-index",
            str(max(1, int(intro_index or 1))),
            "--output-markdown",
            str(output_markdown),
            "--manifest-output",
            str(manifest_output),
            "--registry-path",
            str(registry_dir / "audio_segment_registry.json"),
            "--assembly-mode",
            "top3" if mode == "top" else "standard",
        ]
        if safe_text(account.get("account_id")):
            cmd += ["--account-id", safe_text(account.get("account_id"))]
        if safe_text(project.get("category_name")):
            cmd += ["--category", safe_text(project.get("category_name"))]
        for arg_name, field_name in [
            ("--category-id", "category_id"),
            ("--scheme-id", "scheme_id"),
            ("--scheme-name", "scheme_name"),
        ]:
            value = safe_text(project.get(field_name))
            if value:
                cmd += [arg_name, value]
        if product_uids:
            cmd += ["--uids", ",".join(product_uids)]
        if top_uids:
            cmd += ["--top3-uids", ",".join(top_uids)]
        return cmd

    def build_jianying_command(self, project_id: int, *, draft_name: str = "", spoken_markdown_path: str | Path | None = None) -> list[str]:
        project = self.repo.project(project_id)
        if not project:
            raise ValueError("请先选择品类项目。")
        output_markdown = self._spoken_markdown_path(project, spoken_markdown_path)
        manifest = manifest_path_for_markdown(output_markdown)
        return [
            "python",
            str(legacy_script_path("scripts", "generate_jianying_draft_with_display_videos.py")),
            "--manifest",
            str(manifest),
            "--draft-name",
            safe_path_component(draft_name or safe_text(project.get("name")) or "B-Workflow-SQL"),
            "--draft-root",
            str(DEFAULT_JIANYING_DRAFT_ROOT),
            "--allow-replace",
        ]

    def run_command(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8", errors="replace")

    def _resolve_account(self, label: str) -> dict[str, Any]:
        accounts = self.repo.accounts()
        if label:
            for account in accounts:
                if account["label"] == label:
                    return account
        return accounts[0] if accounts else {}

    def _internal_project_dir(self, project_id: int) -> Path:
        target = INTERNAL_WORKSPACE_ROOT / f"project-{project_id}"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _spoken_markdown_path(self, project: dict[str, Any], explicit_path: str | Path | None = None) -> Path:
        path_text = safe_text(explicit_path) or safe_text(project.get("spoken_md_path"))
        if not path_text:
            raise ValueError("请先在“组合口播稿”里选择口播稿输出 MD。")
        path = Path(path_text)
        if path.suffix.casefold() != ".md":
            raise ValueError("口播稿输出文件必须是 .md 文档。")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def safe_path_component(value: str) -> str:
    text = safe_text(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "B-Workflow-SQL"


def manifest_path_for_markdown(markdown_path: Path) -> Path:
    return markdown_path.with_name(f"{markdown_path.stem}.manifest.json")
