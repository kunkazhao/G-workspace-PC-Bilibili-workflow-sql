# -*- coding: utf-8 -*-
"""通用换音色脚本：更换某个用户的配音音色（IndexTTS 本地 + MiniMax API 两端）。

用法：编辑下面 CONFIG 区的常量，然后用 IndexTTS 的 python 运行：
    G:/Tools/IndexTTS2.0/wzf310/python.exe -X utf8 scripts/swap_voice.py

设计要点（见 docs/更换配音音色操作手册.md）：
  - 中文路径全部在 Python 内部用 pathlib 处理，不经 shell 传递。
  - 只输出纯 ASCII 标志位，结尾做自校验（控制台中文易乱码/被污染）。
  - MiniMax 的 voice_id 不能覆盖，换音色必须用一个平台上不存在的新 voice_id。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

# ============================ CONFIG（每次换音色改这里） ============================
ACCOUNT_LABEL = "小歪"                                   # accounts.label（页面显示名）
INDEXTTS_VOICE_ID = "小歪"                               # voice_profiles.voice_id（通常同 label）
NEW_AUDIO = Path(r"G:\Tools\自己用的音色\小歪10秒新.mp3")  # 新参考音频（mp3/wav，建议 10秒~5分钟，<20MB）

DO_MINIMAX = True                                         # 是否同时换 MiniMax
NEW_MINIMAX_VOICE_ID = "xiaowai-v6"                      # 必须是平台上【不存在】的新 id（旧 id 不能覆盖）
OLD_MINIMAX_VOICE_ID = "xiaowai-v4"                      # 当前 accounts.minimax_voice_id，用于同步两处别名表

# ============================ 固定路径（一般不用改） ============================
DB_PATH = Path(r"G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db")
VOICES_JSON = Path(r"G:\Tools\IndexTTS2.0\outputs\voices\voices.json")
WORKFLOW_SERVICE = Path(r"G:\workspace\PC-Bilibili-workflow-sql\bworkflow_sql\workflow_service.py")
T2A_CORE = Path(r"C:\Users\zhaoer\.codex\skills\minimax-tts\scripts\t2a_core.py")
ENV_PATH = Path(r"C:\Users\zhaoer\.codex\skills\minimax-tts\.env")

MINIMAX_API = "https://api.minimaxi.com"


# ------------------------------- 工具函数 -------------------------------
def fingerprint(p: Path) -> dict:
    st = p.stat()
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return {"sha256": h.hexdigest(), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def env(key: str) -> str:
    import os
    v = os.getenv(key)
    if v:
        return v
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(key):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"缺少 {key}")


# ------------------------------- IndexTTS 端 -------------------------------
def update_indextts() -> dict:
    fp = fingerprint(NEW_AUDIO)
    audio = str(NEW_AUDIO)

    # 1) voices.json（重算指纹，否则服务端可能用旧缓存）
    data = json.loads(VOICES_JSON.read_text(encoding="utf-8"))
    now_j = time.strftime("%Y-%m-%dT%H:%M:%S+0800")
    found = False
    for e in data["voices"]:
        if e.get("voice_id") == INDEXTTS_VOICE_ID:
            e["speaker_audio_path"] = audio
            e["source_audio_path"] = audio
            e["speaker_audio_fingerprint"] = fp
            e["emotion_audio_path"] = None
            e["updated_at"] = now_j
            found = True
            break
    if not found:
        data["voices"].append({
            "voice_id": INDEXTTS_VOICE_ID, "display_name": INDEXTTS_VOICE_ID,
            "speaker_audio_path": audio, "emotion_audio_path": None,
            "source_audio_path": audio, "speaker_audio_fingerprint": fp,
            "created_at": now_j, "updated_at": now_j,
        })
    VOICES_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) voice_profiles
    conn = sqlite3.connect(DB_PATH)
    now_db = time.strftime("%Y%m%d%H%M%S")
    rc = conn.execute(
        "UPDATE voice_profiles SET speaker_audio_path=?, source_audio_path=?, updated_at=? WHERE voice_id=?",
        (audio, audio, now_db, INDEXTTS_VOICE_ID),
    ).rowcount
    conn.commit()
    conn.close()
    return {"fp": fp, "vp_rows": rc, "json_found": found}


# ------------------------------- MiniMax 端 -------------------------------
def clone_minimax() -> dict:
    import requests
    api = env("MINIMAX_API_KEY")
    try:
        gid = env("MINIMAX_GROUP_ID")
    except RuntimeError:
        gid = ""
    auth = {"Authorization": f"Bearer {api}"}

    # 0) 平台已有 voice_id 列表，校验新 id 未被占用
    r = requests.post(f"{MINIMAX_API}/v1/get_voice", headers=auth,
                      json={"voice_type": "all"}, timeout=30)
    r.raise_for_status()
    existing = {v.get("voice_id") for v in (r.json().get("voice_cloning") or [])}
    if NEW_MINIMAX_VOICE_ID in existing:
        return {"ok": False, "reason": "NEW_ID_ALREADY_EXISTS"}

    # 1) 上传音频
    upload_url = f"{MINIMAX_API}/v1/files/upload" + (f"?GroupId={gid}" if gid else "")
    with NEW_AUDIO.open("rb") as fh:
        up = requests.post(upload_url, headers=auth,
                           files={"file": (NEW_AUDIO.name, fh, "application/octet-stream")},
                           data={"purpose": "voice_clone"}, timeout=120)
    up.raise_for_status()
    file_id = (up.json().get("file") or {}).get("file_id")
    if not file_id:
        return {"ok": False, "reason": "UPLOAD_FAILED"}

    # 2) 克隆（带 text 试听即激活计费）
    clone_url = f"{MINIMAX_API}/v1/voice_clone" + (f"?GroupId={gid}" if gid else "")
    cl = requests.post(clone_url,
                       headers={**auth, "Content-Type": "application/json"},
                       json={"file_id": file_id, "voice_id": NEW_MINIMAX_VOICE_ID,
                             "model": "speech-2.8-hd", "text": f"{ACCOUNT_LABEL}音色测试。"},
                       timeout=180)
    cl.raise_for_status()
    if cl.json().get("base_resp", {}).get("status_code", -1) != 0:
        return {"ok": False, "reason": str(cl.json())}

    # 3) accounts.minimax_voice_id
    conn = sqlite3.connect(DB_PATH)
    rc = conn.execute("UPDATE accounts SET minimax_voice_id=? WHERE label=?",
                      (NEW_MINIMAX_VOICE_ID, ACCOUNT_LABEL)).rowcount
    conn.commit()
    conn.close()

    # 4) 两处别名表（OLD -> NEW）：workflow_service.py 文本、t2a_core.py 二进制
    ws = WORKFLOW_SERVICE.read_text(encoding="utf-8")
    if OLD_MINIMAX_VOICE_ID in ws:
        WORKFLOW_SERVICE.write_text(ws.replace(OLD_MINIMAX_VOICE_ID, NEW_MINIMAX_VOICE_ID), encoding="utf-8")
    if T2A_CORE.exists():
        raw = T2A_CORE.read_bytes()
        old_b = OLD_MINIMAX_VOICE_ID.encode()
        if old_b in raw:
            T2A_CORE.write_bytes(raw.replace(old_b, NEW_MINIMAX_VOICE_ID.encode()))
    return {"ok": True, "file_id": file_id, "acc_rows": rc}


# ------------------------------- 主流程 -------------------------------
def main() -> int:
    assert NEW_AUDIO.exists(), f"新音频不存在: {NEW_AUDIO}"
    it = update_indextts()
    print("INDEXTTS_VP_ROWS=", it["vp_rows"])
    print("INDEXTTS_SHA12=", it["fp"]["sha256"][:12])
    print("INDEXTTS_SIZE=", it["fp"]["size"])

    mm = {"ok": None}
    if DO_MINIMAX:
        mm = clone_minimax()
        print("MINIMAX_OK=", int(bool(mm.get("ok"))))
        if not mm.get("ok"):
            print("MINIMAX_REASON=", mm.get("reason"))

    # 自校验（纯 ASCII）
    conn = sqlite3.connect(DB_PATH)
    sp = conn.execute("SELECT speaker_audio_path FROM voice_profiles WHERE voice_id=?",
                      (INDEXTTS_VOICE_ID,)).fetchone()[0]
    acc = conn.execute("SELECT minimax_voice_id FROM accounts WHERE label=?",
                       (ACCOUNT_LABEL,)).fetchone()
    conn.close()
    data2 = json.loads(VOICES_JSON.read_text(encoding="utf-8"))
    je = [e for e in data2["voices"] if e.get("voice_id") == INDEXTTS_VOICE_ID][0]
    ok = (sp == str(NEW_AUDIO)) and (je["speaker_audio_fingerprint"]["sha256"] == it["fp"]["sha256"])
    if DO_MINIMAX and mm.get("ok"):
        ok = ok and (acc and acc[0] == NEW_MINIMAX_VOICE_ID)
    print("VERIFY_DB_JSON_OK=", int(ok))
    print("CURRENT_MINIMAX=", acc[0] if acc else "NULL")
    print("SWAP_DONE=" + ("1" if ok else "0"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
