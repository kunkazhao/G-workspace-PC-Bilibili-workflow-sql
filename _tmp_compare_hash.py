"""对比有线耳机 script_blocks 和 voice asset_bindings 的 text_hash。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # script_blocks 的 hash
    blocks = conn.execute(
        "SELECT id, owner_uid, block_label, body, text_hash FROM script_blocks WHERE project_id=1 AND active=1 AND script_type='product'"
    ).fetchall()

    # voice bindings 的 hash
    bindings = conn.execute(
        "SELECT uid, account_label, block_label, text_hash, path FROM asset_bindings WHERE project_id=1 AND asset_type='voice' AND uid NOT IN ('INTRO', 'PRICE_TRANSITION')"
    ).fetchall()

    # 建立 uid -> block 映射
    block_map = {b["owner_uid"]: b for b in blocks}

    # 建立 (uid, account_label) -> binding 映射
    binding_map = {}
    for b in bindings:
        key = (b["uid"], b["account_label"])
        binding_map[key] = b

    print("UID | 用户 | block_hash | binding_hash | 一致?")
    print("-" * 70)
    for block in blocks:
        uid = block["owner_uid"]
        block_hash = block["text_hash"] or ""
        for user in ["小燃", "小歪", "小博"]:
            binding = binding_map.get((uid, user))
            if binding:
                binding_hash = binding["text_hash"] or ""
                match = "一致" if block_hash == binding_hash else "不一致"
                if match == "不一致":
                    print(f"{uid} | {user} | {block_hash[:12]}... | {binding_hash[:12]}... | {match}")
                    print(f"  block body 前50字: {block['body'][:50]}")
            else:
                print(f"{uid} | {user} | {block_hash[:12]}... | 无绑定 | 缺失")

    conn.close()


if __name__ == "__main__":
    main()
