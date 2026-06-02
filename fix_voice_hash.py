"""修复有线耳机项目 voice 绑定中缺失的 text_hash。"""
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
    ts = now_iso()

    # uid -> script_block hash
    blocks = conn.execute(
        "SELECT id, owner_uid, text_hash FROM script_blocks WHERE project_id=1 AND active=1 AND script_type='product'"
    ).fetchall()
    uid_hash = {b["owner_uid"]: b["text_hash"] for b in blocks}

    # 找 text_hash 为空的 voice 绑定
    bindings = conn.execute(
        "SELECT id, uid, text_hash FROM asset_bindings WHERE project_id=1 AND asset_type='voice' AND uid NOT IN ('INTRO', 'PRICE_TRANSITION')"
    ).fetchall()

    fixed = 0
    for b in bindings:
        if b["text_hash"]:
            continue  # 已有 hash
        if b["uid"] not in uid_hash:
            continue
        conn.execute(
            "UPDATE asset_bindings SET text_hash=?, updated_at=? WHERE id=?",
            (uid_hash[b["uid"]], ts, b["id"]),
        )
        fixed += 1

    conn.commit()
    print(f"已修复 {fixed} 条绑定的 text_hash")
    conn.close()


if __name__ == "__main__":
    main()
