# -*- coding: utf-8 -*-
"""11 段文案 + 小歪 xiaowai-v4 批量配音."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VOICE_ID = "xiaowai-v4"  # 小歪(新克隆)
OUT_DIR = Path(r"G:\2026项目-b站\0611-剃须刀横测-小歪\配音")
SPEED = 1.0
VOL = 1.0
PITCH = 0

SEGMENTS = [
    (1, "01-引言",
     "大家催了又催的电动剃须刀评测，今天终于来交作业了！本期视频呢，我直接把评论区点名最多的热销新型全拉出来，挨个测评一下，一共八个品牌。"),
    (2, "02-选购科普",
     "买电动剃须刀，很多人第一步就选错了。不是先看哪个牌子更响，也不是看宣传里马达多猛，而是先搞清楚你适合往复式，还是旋转式。简单说，往复式更像来回切割，剃得更干净，尤其适合胡子硬、胡量多的人，旋转式胜在贴脸感更柔和，噪音相对小一点，适合胡子软，每天只是简单修一修的人。"),
    (3, "03-总测介绍",
     "这期我一共测了8款电动剃须刀，不只看剃完干不干净，还会看夹不夹胡子，贴脸舒不舒服，脖子和下巴这种难剃位置能不能处理好。接下来呢，我会一个一个介绍，直接告诉你配置怎么样，适合谁用，值不值得买。视频内容比较长，大家可以拉动进度条，跳到感兴趣的位置，话不多说，直接开整！"),
    (4, "04-FESX",
     "第一款是FESX AEX剃须刀。这款我在便携剃须刀里我是非常推荐，因为它不是那种只做小巧外壳，剃起来却软绵绵的迷你款。这个品牌本身有高端精工装备背景，专攻极客精工发烧，它虽然不是超大牌，却用平价死磕千元性能，用过的人却评为200元封神般存在。被粉丝俗称：超级发烧小钢炮。为了打破低价无好货。拒绝比拼花哨智能化，创新7大发烧性能黑科技，超常规11道刀锋硬化技术、8K顺滑刀面工艺、不止单触点的多触点自研磨锋利技术，720°浮动刀头等。动力更硬核，超猛18000转每min，千元内普遍6000-10000转，非单核有刷，而是双核金属无刷电机。用料更一流，德国进口刀片刀头、0.06mm超薄刀片、精工金属机身、不止一层的双层金属精密封装，更牛7防耐用，防生锈、防夹须、防老化、防水等。剃净度达98%的千元标准，夹须率降至0.07%，锋利持久性提升63%，动力衰减降低83%。全金属机身、干湿双剃、磁吸刀头，还有六十分钟续航和五分钟闪充，也让它不只是备用机。想要小巧，又不想牺牲剃净度和耐用性，这款非常值得优先看。"),
    (5, "05-有色小魔方",
     "下一款有色小魔方。这款适合经常出差、想把剃须和鼻毛修剪放在一个小机器里解决的人。它的卖点不是剃须性能多猛，而是口袋装体积和二合一玩法，放进洗漱包或者随身包里都很轻松。旋转式单头设计，配不锈钢刀头，五十分钟续航，日常短时间补剃没有问题。每分钟六千八百转的转速，比特别基础的便携款更有底气，但因为它是单头结构，面对胡子特别密、特别硬的人，效率还是不如后面的高性能款。它更像是一把轻便工具，适合旅行、宿舍、办公室临时整理，或者本来胡子就不多的人。你如果想要功能多一点、体积小一点，它可以看；如果要主力剃须刀，就不要只冲颜值和小巧下单。"),
    (6, "06-未野MAX",
     "下一款是未野 MAX 剃须刀。这个牌子属于那种专做发烧性能的极限运动品牌，死磕剃须这一件事，听着挺轴，但正因为只做一件事，它把转速、刀片、贴合度这些核心参数，都堆到了千元级水准。不搞颜值时尚设计，砍掉多余噱头功能，10多年把剃须功能做到非常规发烧。深受剃须要求高的运动人群喜爱，对不伤肤、卡须、发烧性能要求极高。执行超五倍的非常规性能严苛标准，实现惊人的23000转，每min，罕见做到剃须残留率不足0.04%，胡须残留长度降低98.2%，避免卡肤和伤肤敏感，兼容36种脸型、24种胡须硬度与密度。市场上大多数剃须刀转速停在大几千转，它直接干到 23000 转，纯铜动力引擎配合德国进口自研磨刀片，剃须残留率能压到 0.04% 以下，基本就是上脸一次，干净到底的节奏。三层浮动结构加上双环弧面刀网，让下巴拐角、痘痘边缘这些盲区，能贴得很彻底。特创11大非常规发烧黑科技，如德国进口不锈钢双抗磨损刀片刀网、DOD三重浮动结构、双通路动力、八重特种刀刃、刀面硬化设计等。90 分钟续航加 5 分钟闪充，出差党用也省心。想在几百块钱的价格，拿到发烧级剃须体验的，这一款基本可以闭眼入。"),
    (7, "07-松下",
     "下一款是松下。到了三百元档，如果你更相信老牌往复式的成熟体验，可以看看这款。它是精钢三刀头，刀网厚度做到零点零五五毫米，比普通入门款更贴近皮肤，剃下巴和脸颊时会更利落。柔速双模式是它比较实用的地方，胡子少的时候可以更柔和，胡子多一点也能切到更干净。不过它的电机数据不算太高，每分钟八千七百转，一百三十万次切割，放在现在这批产品里不是性能最猛的。它适合喜欢松下品牌、想要稳定往复式体验的人。"),
    (8, "08-博朗5系",
     "下一款是博朗5系。它适合喜欢德系老牌、又想要硬朗外观的人。博朗的优势一直是刀头锋利、剃感比较干脆，这款也延续了这种路线。智能感应可以根据胡须情况调整输出，六十分钟续航也够日常使用。刀网厚度是零点零三八毫米，贴脸感会比很多厚刀网机型更细，剃脸颊和下巴时不容易有拖沓感。但它的问题也很现实，同价位国产配置已经堆得很满，博朗这款只有单刀头，切割次数是一百二十二万次每分钟，参数上不占优势。它不是配置党首选，更适合看重品牌、手感的人。如果你想要更猛的性能，同价位还可以继续对比。"),
    (9, "09-飞利浦5系",
     "下一款是飞利浦5系。它属于那种老牌大厂主打耐用稳定的款式，五系升级之后加了 AI 调速，刀网做到了 0.046 毫米，基本是贴着脸走也不容易刮伤，7000 转电机，如果不是胡子特别硬，也够用了。适合追求稳定耐用，主要在家里用的人，不锈钢 3 刀头是飞利浦的看家配置，水冲一下就干净，日常维护没什么成本，就是体积偏大，出差塞行李箱不方便，经常出差的人要掂量下。"),
    (10, "10-未野MRX",
     "最后一款是未野 MRX 剃须刀，这款我在高性能剃须刀里重点推荐，之前提到的MAX版本是旋转式，这款是往复式，如果你是络腮胡或者胡子又粗又硬，强烈建议重点看，是往复式里专攻密集胡须的终极方案。配置上是实打实的全面堆料升级，刀头从三刀头直接颠覆到五刀头，被誉为五刀头狂魔。转速从 23000 转拉到 27000 转,每分钟切剃从 350 万次直接翻倍到 685 万次，刀片从 0.06 毫米超薄升级到 0.041 毫米微米级超薄网膜，浮动从三层做到五层全部能浮动，这个配置简直就是离谱。实际体验也非常好，顺滑度非常不错，剃完皮肤不泛红，敏感痘肌都能用。如果你预算卡在千元内，想从旋转式一步到位升级到往复式，或者胡须又密又硬，想直接买一台能镇场子的，强烈推荐给你。"),
    (11, "11-收尾",
     "如果你看完这些还是拿不准该选哪款，或者不知道你的预算最适合哪个，按老规矩在评论区留预算和需求，我看到都会回复。"),
]

def get_api_key() -> str:
    env_path = Path(r"C:\Users\zhaoer\.codex\skills\minimax-tts\.env")
    if env_path.exists():
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
    url = "https://api.minimaxi.com/v1/t2a_v2"
    payload = {
        "model": "speech-2.8-hd",
        "text": text,
        "voice_setting": {
            "voice_id": VOICE_ID,
            "speed": SPEED,
            "vol": VOL,
            "pitch": PITCH,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code}: {err}")
    js = json.loads(body)
    if "data" not in js or "audio" not in js["data"]:
        raise SystemExit(f"unexpected response: {body[:300]!r}")
    audio_hex = js["data"]["audio"]
    out_path.write_bytes(bytes.fromhex(audio_hex))
    out_path.with_suffix(".txt").write_text(text, encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = len(SEGMENTS)
    print(f"开始批量配音: 共 {total} 段, voice={VOICE_ID}, out={OUT_DIR}")
    for idx, (no, name, text) in enumerate(SEGMENTS, start=1):
        out = OUT_DIR / f"{name}.mp3"
        if out.exists() and out.stat().st_size > 1024:
            print(f"[{idx}/{total}] 跳过(已存在): {out.name} ({out.stat().st_size} bytes)")
            continue
        print(f"[{idx}/{total}] 合成中: {name}  ({len(text)} 字)")
        t0 = time.time()
        try:
            synthesize(text, out)
        except SystemExit as e:
            print(f"  失败: {e}")
            return 1
        print(f"  完成: {out.name}  {out.stat().st_size} bytes  {time.time()-t0:.1f}s")
    print("全部完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
