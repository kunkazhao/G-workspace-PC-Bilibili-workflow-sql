"""调试：看 wav 文件名和 script_blocks 的 UID 匹配情况。"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"
VOICE_ROOT = Path(r"G:\2026项目-b站\素材-配音")
CATEGORY_DIR = VOICE_ROOT / "数码-有线耳机"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # product blocks
    blocks = conn.execute(
        "SELECT id, script_type, owner_uid, block_label FROM script_blocks WHERE project_id=1 AND active=1 AND script_type='product'"
    ).fetchall()
    product_uids = {b["owner_uid"] for b in blocks}
    print(f"script_blocks 中的 product UID ({len(product_uids)}):")
    for uid in sorted(product_uids):
        print(f"  {uid}")

    # 已有 voice 绑定
    existing = conn.execute(
        "SELECT uid, account_label, block_label, path FROM asset_bindings WHERE project_id=1 AND asset_type='voice'"
    ).fetchall()
    product_bindings = [e for e in existing if e["uid"] not in ("INTRO", "PRICE_TRANSITION")]
    print(f"\n已有 product voice 绑定 ({len(product_bindings)}):")
    for e in product_bindings:
        print(f"  uid={e['uid']} user={e['account_label']} path={Path(e['path']).name}")

    # wav 文件
    if CATEGORY_DIR.exists():
        for user_dir in sorted(CATEGORY_DIR.iterdir()):
            if not user_dir.is_dir():
                continue
            print(f"\n【{user_dir.name}】wav 文件匹配测试:")
            for wav in sorted(user_dir.glob("*.wav")):
                stem = wav.stem
                uid_match = re.search(r"(YXEJ\d+|YXSB\d+|SB\d+|CDB\d+|PMD\d+|XLB\d+|KX\d+|TZZ\d+)", stem)
                if uid_match:
                    uid = uid_match.group(1)
                    in_blocks = uid in product_uids
                    print(f"  {wav.name}  →  uid={uid}  in_blocks={in_blocks}")
                elif "INTRO" in stem or "引言" in stem:
                    print(f"  {wav.name}  →  INTRO")
                elif "价格" in stem:
                    print(f"  {wav.name}  →  PRICE_TRANSITION")
                else:
                    print(f"  {wav.name}  →  NO MATCH")

    conn.close()


if __name__ == "__main__":
    main()
