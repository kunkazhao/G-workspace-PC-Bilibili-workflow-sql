"""
根据品类和用户批量生成 B 站配音。

默认只同步并预览；确认后加 --execute 才会真正调用 TTS 生成音频。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bworkflow_sql.db import Database
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.utils import safe_text
from bworkflow_sql.workflow_service import WorkflowService


def parse_users(value: str) -> list[str]:
    users = [item.strip() for item in value.replace("，", ",").split(",")]
    return [item for item in users if item]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按品类和用户批量生成配音")
    parser.add_argument("--category", required=True, help="品类/项目名称，如：数码-键盘")
    parser.add_argument("--users", required=True, help="用户列表，逗号分隔，如：小燃,小博")
    parser.add_argument("--execute", action="store_true", help="真正生成配音；不加时只预览")
    parser.add_argument("--skip-sync", action="store_true", help="跳过 Master 和 MD 同步")
    parser.add_argument("--keep-tts", action="store_true", help="执行完成后不关闭 TTS 服务")
    return parser


def project_label(project: dict[str, Any]) -> str:
    parts = [
        safe_text(project.get("name")),
        safe_text(project.get("category_parent_name")),
        safe_text(project.get("category_name")),
        safe_text(project.get("scheme_name")),
        safe_text(project.get("md_path")),
    ]
    return " ".join(part for part in parts if part)


def find_project(db: Database, category: str) -> dict[str, Any]:
    needle = category.casefold()
    projects = [dict(row) for row in db.fetchall("SELECT * FROM projects")]
    matches = [project for project in projects if needle in project_label(project).casefold()]
    if not matches:
        raise ValueError(f"未找到匹配品类/项目：{category}")
    exact_matches = [project for project in matches if safe_text(project.get("name")).casefold() == needle]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(matches) > 1:
        choices = "\n".join(f"- ID={item['id']} {safe_text(item.get('name'))}" for item in matches[:12])
        raise ValueError(f"品类匹配到多个项目，请说得更精确：\n{choices}")
    return matches[0]


def sync_project(sync: SyncService, project_id: int) -> None:
    print("\n=== 1. 同步 Master ===")
    result = sync.sync_master_scheme(project_id, apply_changes=True)
    print(f"新增 {len(result['added'])}，更新 {len(result['updated'])}，移除 {len(result['removed'])}")

    print("\n=== 2. 同步 MD 文案 ===")
    md_result = sync.sync_markdown(project_id)
    print(
        "MD 同步完成："
        f"文案块 {md_result.get('upserted', 0)}，"
        f"缺文案 {len(md_result.get('missing_copy', []))}，"
        f"额外文案 {len(md_result.get('extra_md', []))}"
    )


def preview_user(workflow: WorkflowService, project_id: int, user: str) -> tuple[int, int, int, Path]:
    total, existing, pending = workflow.voice_generation_counts(project_id, account_label=user)
    output_dir = workflow.expected_voice_output_dir(project_id, account_label=user)
    return total, existing, pending, output_dir


def main() -> int:
    args = build_parser().parse_args()
    users = parse_users(args.users)
    if not users:
        raise ValueError("请提供至少一个用户，例如：--users 小燃,小博")

    db = Database()
    sync = SyncService(db)
    workflow = WorkflowService(db)
    project = find_project(db, args.category)
    project_id = int(project["id"])

    print(f"项目：{safe_text(project.get('name'))} (ID={project_id})")
    print(f"用户：{'、'.join(users)}")

    if args.skip_sync:
        print("\n=== 跳过同步 ===")
    else:
        sync_project(sync, project_id)

    print("\n=== 3. 配音预览 ===")
    previews: list[tuple[str, int, int, int, Path]] = []
    for user in users:
        total, existing, pending, output_dir = preview_user(workflow, project_id, user)
        previews.append((user, total, existing, pending, output_dir))
        print(f"{user}：文案 {total} 条，已有跳过 {existing} 条，待生成 {pending} 条")
        print(f"输出目录：{output_dir}")

    pending_total = sum(item[3] for item in previews)
    if not args.execute:
        print("\n预览完成。确认后重新运行同一命令并追加 --execute 执行生成。")
        return 0
    if pending_total == 0:
        print("\n所有目标用户都已有可用配音，无需生成。")
        return 0

    print("\n=== 4. 执行配音生成 ===")
    exit_code = 0
    try:
        for user, _total, _existing, pending, _output_dir in previews:
            if pending == 0:
                print(f"\n--- {user}：无需生成，已跳过 ---")
                continue
            print(f"\n--- {user}：开始生成 {pending} 条 ---")
            result = workflow.generate_voice(
                project_id,
                account_label=user,
                start_service_if_needed=True,
                progress_hook=print,
            )
            if result.stderr:
                print(result.stderr)
            if result.returncode != 0:
                exit_code = result.returncode
    finally:
        if args.keep_tts:
            print("\n=== 5. 保留 TTS 服务运行 ===")
        else:
            print("\n=== 5. 关闭 TTS 服务 ===")
            killed = workflow.shutdown_tts_service()
            print(f"已关闭 {killed} 个配音服务进程" if killed else "无运行中的配音服务")

    print("\n全部完成。")
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"执行失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
