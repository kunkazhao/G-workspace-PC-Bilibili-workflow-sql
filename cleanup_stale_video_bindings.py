"""清理手柄项目中文件已不存在的 video asset_bindings 记录。

将 path 指向不存在文件的 video 绑定标记为 stale。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 找到手柄项目
    projects = conn.execute(
        "SELECT id, name FROM projects WHERE category_name LIKE '%手柄%' OR name LIKE '%手柄%'"
    ).fetchall()

    ts = now_iso()
    total_cleaned = 0

    for p in projects:
        pid = p["id"]
        bindings = conn.execute(
            "SELECT id, uid, path, status FROM asset_bindings WHERE project_id=? AND asset_type='video' AND status='ready'",
            (pid,),
        ).fetchall()

        stale_ids = []
        for b in bindings:
            path_text = b["path"] or ""
            if path_text and not Path(path_text).exists():
                stale_ids.append(b["id"])

        if stale_ids:
            placeholders = ", ".join("?" for _ in stale_ids)
            conn.execute(
                f"UPDATE asset_bindings SET status='stale', updated_at=? WHERE id IN ({placeholders})",
                (ts, *stale_ids),
            )
            print(f"项目 [{pid}] {p['name']}：清理 {len(stale_ids)} 条旧记录")
            total_cleaned += len(stale_ids)
        else:
            print(f"项目 [{pid}] {p['name']}：无需清理")

    conn.commit()

    # 验证
    for p in projects:
        ready = conn.execute(
            "SELECT COUNT(*) as cnt FROM asset_bindings WHERE project_id=? AND asset_type='video' AND status='ready'",
            (p["id"],),
        ).fetchone()
        print(f"\n清理后 - 项目 [{p['id']}] ready 的 video 绑定: {ready['cnt']} 条")

    conn.close()
    print(f"\n共清理 {total_cleaned} 条记录。")


if __name__ == "__main__":
    main()
