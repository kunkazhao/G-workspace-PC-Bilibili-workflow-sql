"""修复有线耳机项目 voice 绑定中缺失的 block_label。"""
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

    # 获取有线耳机的 product script_blocks，建立 uid -> block_label 映射
    blocks = conn.execute(
        "SELECT id, owner_uid, block_label FROM script_blocks WHERE project_id=1 AND active=1 AND script_type='product'"
    ).fetchall()
    uid_to_block = {b["owner_uid"]: b for b in blocks}

    # 查找 block_label 为空的 voice 绑定
    bindings = conn.execute(
        "SELECT id, uid, account_label, block_label, path FROM asset_bindings WHERE project_id=1 AND asset_type='voice'"
    ).fetchall()

    fixed = 0
    for binding in bindings:
        uid = binding["uid"]
        if uid in ("INTRO", "PRICE_TRANSITION"):
            continue
        if binding["block_label"]:
            continue  # 已有 block_label
        if uid not in uid_to_block:
            continue
        block = uid_to_block[uid]
        conn.execute(
            "UPDATE asset_bindings SET block_label=?, script_block_id=?, updated_at=? WHERE id=?",
            (block["block_label"], block["id"], ts, binding["id"]),
        )
        fixed += 1

    conn.commit()

    # 验证
    remaining = conn.execute(
        "SELECT COUNT(*) as cnt FROM asset_bindings WHERE project_id=1 AND asset_type='voice' AND block_label='' AND uid NOT IN ('INTRO', 'PRICE_TRANSITION')"
    ).fetchone()

    print(f"已修复 {fixed} 条绑定的 block_label")
    print(f"剩余 block_label 为空的商品绑定: {remaining['cnt']} 条")

    conn.close()


if __name__ == "__main__":
    main()
