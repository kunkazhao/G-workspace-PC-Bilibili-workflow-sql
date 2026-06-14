# -*- coding: utf-8 -*-
"""一段合并:引言+选购科普+总测介绍, 小歪 xiaowai-v4."""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(r"G:\2026项目-b站\0611-剃须刀横测-小歪\配音\00-完整引言段落.mp3")
TXT_OUT = OUT.with_suffix(".txt")
VOICE_ID = "xiaowai-v4"
TEXT = (
    "大家催了又催的电动剃须刀评测，今天终于来交作业了！本期视频呢，我直接把评论区点名最多的热销新型全拉出来，挨个测评一下，一共八个品牌。"
    "买电动剃须刀，很多人第一步就选错了。不是先看哪个牌子更响，也不是看宣传里马达多猛，而是先搞清楚你适合往复式，还是旋转式。"
    "简单说，往复式更像来回切割，剃得更干净，尤其适合胡子硬、胡量多的人，旋转式胜在贴脸感更柔和，噪音相对小一点，适合胡子软，每天只是简单修一修的人。"
    "这期我一共测了8款电动剃须刀，不只看剃完干不干净，还会看夹不夹胡子，贴脸舒不舒服，脖子和下巴这种难剃位置能不能处理好。"
    "接下来呢，我会一个一个介绍，直接告诉你配置怎么样，适合谁用，值不值得买。视频内容比较长，大家可以拉动进度条，跳到感兴趣的位置，话不多说，直接开整！"
)


def get_api_key() -> str:
    env_path = Path(r"C:\Users\zhaoer\.codex\skills\minimax-tts\.env")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "MINIMAX_API_KEY":
            return v.strip().strip('"').strip("'")
    raise SystemExit("MINIMAX_API_KEY not set")


def synthesize(text: str, out_path: Path) -> None:
    api_key = get_api_key()
    payload = {
        "model": "speech-2.8-hd",
        "text": text,
        "voice_setting": {"voice_id": VOICE_ID, "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/t2a_v2",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code}: {e.read().decode('utf-8','replace')}")
    js = json.loads(body)
    out_path.write_bytes(bytes.fromhex(js["data"]["audio"]))
    out_path.with_suffix(".txt").write_text(text, encoding="utf-8")


def main() -> int:
    print(f"合成 {OUT.name}  ({len(TEXT)} 字, voice={VOICE_ID})")
    t0 = time.time()
    synthesize(TEXT, OUT)
    print(f"完成: {OUT.stat().st_size} bytes  {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
