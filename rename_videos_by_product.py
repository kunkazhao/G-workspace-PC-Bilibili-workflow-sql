"""扫描视频目录，按商品名称匹配数据库中的商品，将视频文件重命名为 价格-uid-商品名称.mp4。

用法：python rename_videos_by_product.py [--dry-run]
  --dry-run  只打印匹配结果，不实际重命名。
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"
VIDEO_DIR = Path(r"G:\2026项目-b站\素材-剪辑\roll-b素材\数码-游戏手柄")
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi"}


def normalize_name(value: str) -> str:
    """去除空格、标点、序号前缀，统一为可比较的小写文本。"""
    text = value.strip()
    # 去掉文件名开头的序号前缀，如 "1-墨将凌云" → "墨将凌云"
    text = re.sub(r"^\d+[\-_\s]+", "", text)
    # 去掉所有空格、标点
    text = re.sub(r"[\s\-_—·,，。、/\\()（）\[\]【】「」『』《》<>]+", "", text)
    return text.casefold()


def safe_filename(value: str) -> str:
    """清理文件名中的非法字符。"""
    text = value.strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return
    if not VIDEO_DIR.exists():
        print(f"视频目录不存在: {VIDEO_DIR}")
        return

    # 1. 查询数据库：找到品类为「游戏手柄」的项目及其商品
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 尝试按 category_name 匹配
    projects = conn.execute(
        "SELECT id, name, category_name FROM projects WHERE category_name LIKE '%手柄%' OR name LIKE '%手柄%'"
    ).fetchall()
    if not projects:
        print("数据库中没有找到「手柄」相关的项目。")
        conn.close()
        return

    print("找到以下手柄项目：")
    for p in projects:
        print(f"  [{p['id']}] {p['name']}（品类：{p['category_name']}）")

    # 汇总所有手柄项目的商品
    all_products: dict[str, dict] = {}  # uid → {title, price_label}
    for p in projects:
        rows = conn.execute(
            "SELECT uid, title, price_label FROM products WHERE project_id=? AND active=1 AND removed_from_master=0",
            (p["id"],),
        ).fetchall()
        for r in rows:
            uid = r["uid"]
            if uid not in all_products:
                all_products[uid] = {"title": r["title"], "price_label": r["price_label"]}

    conn.close()

    if not all_products:
        print("这些项目下没有商品数据。")
        return

    print(f"\n共 {len(all_products)} 个商品：")
    for uid, info in all_products.items():
        print(f"  {uid} | {info['title']} | {info['price_label']}")

    # 2. 扫描视频目录
    video_files = [
        f for f in VIDEO_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_SUFFIXES
    ]
    print(f"\n视频目录下共 {len(video_files)} 个视频文件。")

    # 3. 按商品名称匹配
    # 建立 normalized_name → uid 的映射
    name_to_uid: dict[str, str] = {}
    for uid, info in all_products.items():
        key = normalize_name(info["title"])
        if key:
            name_to_uid[key] = uid

    matched: list[tuple[Path, str, dict]] = []  # (原路径, uid, product_info)
    unmatched: list[Path] = []

    for vf in video_files:
        file_stem = vf.stem  # 不含扩展名
        # 尝试直接匹配（去掉序号前缀后）
        norm_file = normalize_name(file_stem)
        uid = name_to_uid.get(norm_file)
        if uid:
            matched.append((vf, uid, all_products[uid]))
        else:
            # 尝试模糊匹配：文件名包含商品名，或商品名包含文件名
            for uid_candidate, info in all_products.items():
                norm_title = normalize_name(info["title"])
                if norm_title and (norm_title in norm_file or norm_file in norm_title):
                    uid = uid_candidate
                    matched.append((vf, uid, all_products[uid]))
                    break
            else:
                unmatched.append(vf)

    # 4. 打印匹配结果
    print(f"\n匹配结果：成功 {len(matched)}，未匹配 {len(unmatched)}")

    if matched:
        print("\n--- 匹配成功的文件 ---")
        rename_plan: list[tuple[Path, Path]] = []
        for old_path, uid, info in matched:
            price = safe_filename(info["price_label"]) or "未知价格"
            title = safe_filename(info["title"]) or uid
            new_name = f"{price}-{uid}-{title}{old_path.suffix.lower()}"
            new_path = old_path.parent / new_name
            print(f"  {old_path.name}")
            print(f"    → {new_name}")
            if old_path != new_path:
                rename_plan.append((old_path, new_path))

        if rename_plan:
            if dry_run:
                print(f"\n[dry-run] 共 {len(rename_plan)} 个文件需要重命名，加 --dry-run 参数跳过。")
            else:
                print(f"\n开始重命名 {len(rename_plan)} 个文件...")
                for old_path, new_path in rename_plan:
                    if new_path.exists() and old_path != new_path:
                        print(f"  跳过（目标已存在）：{new_path.name}")
                        continue
                    old_path.rename(new_path)
                    print(f"  已重命名：{old_path.name} → {new_path.name}")
                print("重命名完成。")
        else:
            print("\n所有文件名已符合规范，无需重命名。")

    if unmatched:
        print(f"\n--- 未匹配的文件（{len(unmatched)} 个）---")
        for vf in unmatched:
            print(f"  {vf.name}")


if __name__ == "__main__":
    main()
