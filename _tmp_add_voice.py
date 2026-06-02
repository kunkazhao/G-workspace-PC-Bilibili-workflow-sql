"""临时脚本：为「知了」账号添加 voice_profiles 记录。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(r"G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db")
SPEAKER_AUDIO = r"G:\Tools\自己用的音色\知了.wav"

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def main() -> None:
    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. 查找知了账号的 voice_id
    row = conn.execute("SELECT label, voice_id FROM accounts WHERE label=?", ("知了",)).fetchone()
    if not row:
        print("错误：accounts 表中没有找到「知了」账号。请先在 UI 的「用户管理」页面创建。")
        conn.close()
        return

    label = row["label"]
    voice_id = row["voice_id"] or label  # 如果 voice_id 为空，用 label 作为 fallback
    print(f"找到账号: label={label}, voice_id={voice_id}")

    # 2. 检查是否已有 voice_profiles 记录
    existing = conn.execute("SELECT id, voice_id FROM voice_profiles WHERE voice_id=?", (voice_id,)).fetchone()
    ts = now_iso()

    if existing:
        # 更新
        conn.execute(
            "UPDATE voice_profiles SET speaker_audio_path=?, display_name=?, updated_at=? WHERE voice_id=?",
            (SPEAKER_AUDIO, label, ts, voice_id),
        )
        print(f"已更新 voice_profiles: voice_id={voice_id}, speaker_audio_path={SPEAKER_AUDIO}")
    else:
        # 新增
        conn.execute(
            "INSERT INTO voice_profiles (voice_id, display_name, speaker_audio_path, emotion_audio_path, source_audio_path, created_at, updated_at) VALUES (?, ?, ?, '', '', ?, ?)",
            (voice_id, label, SPEAKER_AUDIO, ts, ts),
        )
        print(f"已新增 voice_profiles: voice_id={voice_id}, speaker_audio_path={SPEAKER_AUDIO}")

    conn.commit()

    # 3. 验证
    result = conn.execute("SELECT * FROM voice_profiles WHERE voice_id=?", (voice_id,)).fetchone()
    print(f"\n验证结果:")
    print(f"  voice_id:          {result['voice_id']}")
    print(f"  display_name:      {result['display_name']}")
    print(f"  speaker_audio_path: {result['speaker_audio_path']}")

    # 4. 检查音频文件是否存在
    if Path(SPEAKER_AUDIO).exists():
        size_kb = Path(SPEAKER_AUDIO).stat().st_size / 1024
        print(f"  音频文件:          存在 ({size_kb:.0f} KB)")
    else:
        print(f"  音频文件:          警告 - 文件不存在!")

    conn.close()
    print("\n完成。")

if __name__ == "__main__":
    main()
