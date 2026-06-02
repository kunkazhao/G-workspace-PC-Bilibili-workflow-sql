"""检查指定品类下指定用户的配音缺口。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"

# 第一组：小燃、小歪、小博
GROUP1_CATEGORIES = ["游戏手柄", "耳夹耳机", "入耳耳机", "头戴耳机", "鼠标", "键盘", "有线耳机", "屏幕挂灯"]
GROUP1_USERS = ["小燃", "小歪", "小博"]

# 第二组：知了
GROUP2_CATEGORIES = ["夏凉被", "烤箱", "体脂秤"]
GROUP2_USERS = ["知了"]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("=" * 70)
    print("第一组：小燃 / 小歪 / 小博")
    print("=" * 70)
    for cat in GROUP1_CATEGORIES:
        check_category(conn, cat, GROUP1_USERS)

    print()
    print("=" * 70)
    print("第二组：知了")
    print("=" * 70)
    for cat in GROUP2_CATEGORIES:
        check_category(conn, cat, GROUP2_USERS)

    conn.close()


def check_category(conn: sqlite3.Connection, category: str, users: list[str]) -> None:
    # 找到品类项目
    projects = conn.execute(
        "SELECT id, name, category_name FROM projects WHERE category_name=? OR name=?",
        (category, category),
    ).fetchall()
    if not projects:
        # 模糊匹配
        projects = conn.execute(
            "SELECT id, name, category_name FROM projects WHERE category_name LIKE ? OR name LIKE ?",
            (f"%{category}%", f"%{category}%"),
        ).fetchall()

    if not projects:
        print(f"\n【{category}】未找到对应项目")
        return

    for proj in projects:
        pid = proj["id"]
        print(f"\n【{category}】项目: {proj['name']} (id={pid})")

        # 获取商品列表
        products = conn.execute(
            "SELECT uid, title FROM products WHERE project_id=? AND active=1 AND removed_from_master=0 ORDER BY sort_order, id",
            (pid,),
        ).fetchall()
        product_uids = {row["uid"] for row in products}
        product_titles = {row["uid"]: row["title"] for row in products}

        # 获取所有 script_blocks（product 类型）
        blocks = conn.execute(
            "SELECT id, script_type, owner_uid, block_label, text_hash FROM script_blocks WHERE project_id=? AND active=1",
            (pid,),
        ).fetchall()
        product_blocks = [b for b in blocks if b["script_type"] == "product"]
        intro_blocks = [b for b in blocks if b["script_type"] == "intro"]

        # 获取所有 voice 类型的 asset_bindings
        voice_assets = conn.execute(
            "SELECT uid, account_label, block_label, text_hash, path, status FROM asset_bindings WHERE project_id=? AND asset_type='voice' AND status='ready'",
            (pid,),
        ).fetchall()

        print(f"  商品数: {len(products)} | 文案块: {len(product_blocks)} | 引言: {len(intro_blocks)}")

        for user in users:
            missing = []
            expired = []
            ready_count = 0

            # 检查每个 product block
            for block in product_blocks:
                uid = block["owner_uid"]
                block_label = block["block_label"] or "正文"
                block_hash = block["text_hash"] or ""

                # 查找匹配的 voice asset
                matched = [
                    a for a in voice_assets
                    if a["account_label"] == user
                    and a["uid"] == uid
                    and (not block_label or a["block_label"] == block_label)
                ]

                if not matched:
                    title = product_titles.get(uid, uid)
                    missing.append(f"{uid} {title} / {block_label}")
                elif block_hash:
                    hash_match = any(a["text_hash"] == block_hash and Path(a["path"]).exists() for a in matched)
                    if not hash_match:
                        title = product_titles.get(uid, uid)
                        expired.append(f"{uid} {title} / {block_label}")
                    else:
                        ready_count += 1
                else:
                    ready_count += 1

            # 检查引言
            for block in intro_blocks:
                block_label = block["block_label"] or "引言"
                block_hash = block["text_hash"] or ""
                matched = [
                    a for a in voice_assets
                    if a["account_label"] == user
                    and a["uid"] == "INTRO"
                    and (not block_label or a["block_label"] == block_label)
                ]
                if not matched:
                    missing.append(f"INTRO / {block_label}")
                elif block_hash:
                    hash_match = any(a["text_hash"] == block_hash and Path(a["path"]).exists() for a in matched)
                    if not hash_match:
                        expired.append(f"INTRO / {block_label}")
                    else:
                        ready_count += 1
                else:
                    ready_count += 1

            total = ready_count + len(missing) + len(expired)
            status_parts = [f"就绪 {ready_count}"]
            if missing:
                status_parts.append(f"缺 {len(missing)}")
            if expired:
                status_parts.append(f"过期 {len(expired)}")

            status_str = " | ".join(status_parts)
            print(f"  [{user}] 总 {total} 块，{status_str}")

            if missing:
                for item in missing:
                    print(f"    缺: {item}")
            if expired:
                for item in expired:
                    print(f"    过期: {item}")


if __name__ == "__main__":
    main()
