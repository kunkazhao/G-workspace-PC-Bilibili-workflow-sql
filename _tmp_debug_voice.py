"""诊断有线耳机项目的配音文件匹配情况。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "bworkflow.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. 找到有线耳机项目
    proj = conn.execute(
        "SELECT id, name, voice_root FROM projects WHERE id=1"
    ).fetchone()
    if not proj:
        print("未找到有线耳机项目")
        return

    pid = proj["id"]
    voice_root = proj["voice_root"] or ""
    print(f"项目: {proj['name']} (id={pid})")
    print(f"配音根目录: {voice_root}")

    # 2. 检查配音根目录是否存在
    if voice_root:
        vr = Path(voice_root)
        if vr.exists():
            wav_files = list(vr.rglob("*.wav"))
            print(f"配音目录下 wav 文件数: {len(wav_files)}")
            for f in wav_files[:30]:
                rel = f.relative_to(vr) if f.is_relative_to(vr) else f
                print(f"  {rel}")
            if len(wav_files) > 30:
                print(f"  ... 共 {len(wav_files)} 个")
        else:
            print(f"配音目录不存在: {voice_root}")
    else:
        print("配音根目录未配置")

    # 3. 查数据库中的 voice asset_bindings
    voice_bindings = conn.execute(
        "SELECT id, uid, account_label, block_label, path, status, text_hash FROM asset_bindings WHERE project_id=? AND asset_type='voice'",
        (pid,),
    ).fetchall()
    print(f"\n数据库中 voice 绑定记录数: {len(voice_bindings)}")
    for b in voice_bindings[:20]:
        exists = Path(b["path"]).exists() if b["path"] else False
        print(f"  uid={b['uid']} user={b['account_label']} label={b['block_label']} status={b['status']} exists={exists} path={b['path']}")

    # 4. 查 script_blocks
    blocks = conn.execute(
        "SELECT id, script_type, owner_uid, block_label, text_hash FROM script_blocks WHERE project_id=? AND active=1",
        (pid,),
    ).fetchall()
    print(f"\n文案块数: {len(blocks)}")
    for b in blocks:
        print(f"  type={b['script_type']} uid={b['owner_uid']} label={b['block_label']} hash={b['text_hash'][:8] if b['text_hash'] else '无'}")

    conn.close()


if __name__ == "__main__":
    main()
