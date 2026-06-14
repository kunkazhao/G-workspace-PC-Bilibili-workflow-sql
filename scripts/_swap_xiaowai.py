# -*- coding: utf-8 -*-
"""把 DB 和 IndexTTS voices.json 里的小歪换成新参考音频."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

NEW_AUDIO = Path(r"G:\Tools\自己用的音色\小歪10秒.mp3")
OLD_ITTS_FILE = Path(r"G:\Tools\IndexTTS2.0\outputs\voices\小歪-5662aef1f3.mp3")
OLD_SOURCE_FILE = Path(r"G:\Tools\自己用的音色\小歪（10秒）.mp3")  # 旧全角括号
VOICES_JSON = Path(r"G:\Tools\IndexTTS2.0\outputs\voices\voices.json")
DB_PATH = Path(r"G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db")


def fingerprint(p: Path) -> dict:
    st = p.stat()
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return {
        "sha256": h.hexdigest(),
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }


def update_voices_json() -> None:
    data = json.loads(VOICES_JSON.read_text(encoding="utf-8"))
    fp = fingerprint(NEW_AUDIO)
    new_audio_str = str(NEW_AUDIO)
    now = time.strftime("%Y-%m-%dT%H:%M:%S+0800", time.localtime())
    found = False
    for entry in data["voices"]:
        if entry.get("voice_id") == "小歪":
            entry["speaker_audio_path"] = new_audio_str
            entry["source_audio_path"] = new_audio_str
            entry["speaker_audio_fingerprint"] = fp
            entry["emotion_audio_path"] = None
            entry["updated_at"] = now
            found = True
            break
    if not found:
        data["voices"].append({
            "voice_id": "小歪",
            "display_name": "小歪",
            "speaker_audio_path": new_audio_str,
            "emotion_audio_path": None,
            "source_audio_path": new_audio_str,
            "speaker_audio_fingerprint": fp,
            "created_at": now,
            "updated_at": now,
        })
    VOICES_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  voices.json: 小歪 -> {new_audio_str}")
    print(f"  sha256: {fp['sha256']}")


def update_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    new_audio_str = str(NEW_AUDIO)
    now = time.strftime("%Y%m%d%H%M%S", time.localtime())
    cur.execute(
        "UPDATE voice_profiles "
        "SET speaker_audio_path=?, source_audio_path=?, updated_at=? "
        "WHERE voice_id='小歪'",
        (new_audio_str, new_audio_str, now),
    )
    if cur.rowcount == 0:
        print("  DB: 小歪 不存在,跳过")
    else:
        print(f"  DB: 小歪 -> {new_audio_str} (rows={cur.rowcount})")
    conn.commit()
    conn.close()


def cleanup_old() -> None:
    for p in [OLD_ITTS_FILE, OLD_SOURCE_FILE]:
        if p.exists():
            p.unlink()
            print(f"  删除: {p}")
        else:
            print(f"  不存在跳过: {p}")


def main() -> int:
    assert NEW_AUDIO.exists(), f"新音频不存在: {NEW_AUDIO}"
    print("== 更新 IndexTTS voices.json ==")
    update_voices_json()
    print("\n== 更新 DB voice_profiles ==")
    update_db()
    print("\n== 清理旧文件 ==")
    cleanup_old()
    print("\n== 完成 ==")
    print("MiniMax 端还需在 MiniMax 平台用新音频克隆 xiaowao-v3 后才能用.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
