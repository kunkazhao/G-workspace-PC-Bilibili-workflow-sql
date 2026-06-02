"""查询「知了」账号的完整信息。"""
from __future__ import annotations
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"

def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    row = conn.execute("SELECT * FROM accounts WHERE label=?", ("知了",)).fetchone()
    if not row:
        print("未找到「知了」账号")
        conn.close()
        return

    print("=== accounts 表 ===")
    for key in row.keys():
        print(f"  {key}: {row[key]}")

    closing = row["closing_audio_path"]
    if closing:
        exists = Path(closing).exists()
        print(f"\n结尾音频路径: {closing}")
        print(f"文件存在: {exists}")
    else:
        print("\n结尾音频路径: (空)")

    conn.close()

if __name__ == "__main__":
    main()
