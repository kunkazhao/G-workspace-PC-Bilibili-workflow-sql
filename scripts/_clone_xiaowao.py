# -*- coding: utf-8 -*-
"""调 MiniMax voice_clone 接口把 小歪10秒.mp3 克隆成 xiaowao-v3."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API_KEY = "sk-cp-7tQq3ksXhR0S-H8QrtduWs2WQBmngutnUetuMR9l81x2F9vNUn01vD2u5m0UMfC9O2udisagQHvDWJFJaiTJqyUNZgchY5o-i0bUI_i-fMaKcBJhkNnrehg"
AUDIO = Path(r"G:\Tools\自己用的音色\小歪10秒.mp3")
NEW_VOICE_ID = "xiaowao-v3"


def call_clone() -> dict:
    # multipart/form-data 手工拼
    boundary = "----MavisFormBoundary123"
    body = []
    # file
    body.append(f"--{boundary}\r\n".encode())
    body.append(
        f'Content-Disposition: form-data; name="audio"; filename="{AUDIO.name}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n".encode()
    )
    body.append(AUDIO.read_bytes())
    body.append(b"\r\n")
    # voice_id
    body.append(f"--{boundary}\r\n".encode())
    body.append(
        f'Content-Disposition: form-data; name="voice_id"\r\n\r\n{NEW_VOICE_ID}\r\n'.encode()
    )
    # model
    body.append(f"--{boundary}\r\n".encode())
    body.append(
        'Content-Disposition: form-data; name="model"\r\n\r\nspeech-2.8-hd\r\n'.encode()
    )
    # text (clone 必填,但这个字段在 clone 接口里可能不需要,先带上)
    body.append(f"--{boundary}\r\n".encode())
    body.append(
        'Content-Disposition: form-data; name="text"\r\n\r\n小歪\r\n'.encode()
    )
    # need_noise_reduction
    body.append(f"--{boundary}\r\n".encode())
    body.append(
        'Content-Disposition: form-data; name="need_noise_reduction"\r\n\r\ntrue\r\n'.encode()
    )
    # need_volume_normalization
    body.append(f"--{boundary}\r\n".encode())
    body.append(
        'Content-Disposition: form-data; name="need_volume_normalization"\r\n\r\ntrue\r\n'.encode()
    )
    body.append(f"--{boundary}--\r\n".encode())
    data = b"".join(body)

    req = urllib.request.Request(
        "https://api.MiniMax.chat/v1/voice_clone",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_body": e.read().decode("utf-8", errors="replace")}


def main() -> int:
    if not AUDIO.exists():
        print(f"audio missing: {AUDIO}")
        return 1
    print(f"audio: {AUDIO}  ({AUDIO.stat().st_size} bytes)")
    print(f"new voice_id: {NEW_VOICE_ID}")
    print("调用 voice_clone ...")
    r = call_clone()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("base_resp", {}).get("status_code", 0) == 0 or r.get("voice_id"):
        print("\n克隆成功")
    else:
        print("\n克隆失败")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
