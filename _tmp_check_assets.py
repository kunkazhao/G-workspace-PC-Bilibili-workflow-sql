"""诊断：查看手柄项目的 asset_bindings 视频记录。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 找到手柄项目
    projects = conn.execute(
        "SELECT id, name, category_name FROM projects WHERE category_name LIKE '%手柄%' OR name LIKE '%手柄%'"
    ).fetchall()
    if not projects:
        print("未找到手柄项目")
        conn.close()
        return

    for p in projects:
        pid = p["id"]
        print(f"=== 项目 [{pid}] {p['name']}（{p['category_name']}）===")

        # 所有 video 类型的 asset_bindings
        bindings = conn.execute(
            "SELECT id, uid, path, status, asset_type, account_label, created_at, updated_at "
            "FROM asset_bindings WHERE project_id=? AND asset_type='video' ORDER BY id",
            (pid,),
        ).fetchall()
        print(f"  video 绑定总数: {len(bindings)}")
        for b in bindings:
            exists = Path(b["path"]).exists() if b["path"] else False
            print(f"  [{b['id']}] uid={b['uid']} status={b['status']} exists={exists} path={b['path']}")

        # 按 status 统计
        status_counts = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM asset_bindings WHERE project_id=? AND asset_type='video' GROUP BY status",
            (pid,),
        ).fetchall()
        print(f"  按状态统计: {dict((r['status'], r['cnt']) for r in status_counts)}")

        # 商品数
        products = conn.execute(
            "SELECT COUNT(*) as cnt FROM products WHERE project_id=? AND active=1 AND removed_from_master=0",
            (pid,),
        ).fetchone()
        print(f"  活跃商品数: {products['cnt']}")

        print()

    conn.close()


if __name__ == "__main__":
    main()
