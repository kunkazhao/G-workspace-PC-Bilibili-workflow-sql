# -*- coding: utf-8 -*-
"""Batch MiniMax TTS for 充电宝 project - products + category transitions."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bworkflow_sql.db import Database
from bworkflow_sql.repositories import Repository
from bworkflow_sql.workflow_service import WorkflowService


def main():
    db = Database(str(Path(__file__).resolve().parent.parent / "data" / "bworkflow.db"))
    repo = Repository(db)
    ws = WorkflowService(db)

    project_id = 14
    voice_id = "rongrong-v2"
    output_dir = Path(r"G:\2026项目-b站\素材-配音\数码-充电宝\荣荣")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get all product script blocks
    blocks = repo.script_blocks(project_id)
    products = {
        p["uid"]: p
        for p in repo.products(project_id, include_removed=False)
    }

    # Category transition texts from MD
    category_transitions = [
        {
            "name": "高性价比款",
            "text": "先看高性价比款。这几款是我从这些充电宝里挑出来，日常使用最推荐的。不是最便宜的，也不是最猛的，而是在价格、容量、快充、便携性之间，找到了很好的平衡点。不管你是学生党还是上班族，不想花太多时间研究对比，直接从这个段位里选一个，基本不会出错，不用反复比来比去。",
        },
        {
            "name": "日常款",
            "text": "下面看日常款。这个段位的充电宝属于六边形战士，不重不轻，容量够用，快充够快，带着出门没有太大负担。没有特别突出的长板，但也没有明显的短板，适合大部分人的日常实用和短途出行，不知道买什么就先看这个区间。",
        },
        {
            "name": "小巧精致款",
            "text": "下面看小巧精致款。这个段位的充电宝主打一个轻便随身，容量和性能确实比不过那些大块头，但胜在揣兜里就走，完全不累赘。适合对便携性要求高，出门不想背大包的人，日常应急补个电刚刚好。",
        },
        {
            "name": "磁吸便捷款",
            "text": "下面是磁吸款，这种最大的好处就是省心，往手机后面一贴就开始充，不用插线不用翻包，苹果用户的福音。但要注意，磁吸充电宝普遍容量偏小、功率不高，而且无线充电过程中发热是正常现象，选购的时候要注意甄别。",
        },
        {
            "name": "高性能款",
            "text": "下面看高性能款。这个段位的充电宝性能拉满了，100W甚至更高的快充，两万到七万毫安时的超大容量，给笔记本和手机同时供电都没问题。代价就是重量和体积，更适合经常出差带笔记本的商务用户，或者户外露营需要给多设备供电的玩家。",
        },
    ]

    tasks = []

    # Product blocks
    for block in blocks:
        if block["script_type"] != "product":
            continue
        uid = block.get("owner_uid", "")
        if uid not in products:
            print(f"SKIP {uid} - not in active products")
            continue
        product = products[uid]
        price = product.get("price_label", "")
        # Format price label
        try:
            price_num = float(str(price).replace("元", "").replace("¥", "").strip())
            price_str = f"{int(price_num)}" if price_num == int(price_num) else f"{price_num}"
        except ValueError:
            price_str = str(price)
        title = product.get("title", "")
        label = block.get("block_label", "正文")
        filename = f"{price_str}-{uid}-{title}-{label}.mp3"
        filepath = output_dir / filename
        if filepath.exists():
            print(f"SKIP {filename} - already exists")
            continue
        tasks.append({
            "type": "product",
            "uid": uid,
            "text": block["body"],
            "filename": filename,
            "filepath": filepath,
        })

    # Category transitions
    for ct in category_transitions:
        filename = f"0-品类过渡-{ct['name']}.mp3"
        filepath = output_dir / filename
        if filepath.exists():
            print(f"SKIP {filename} - already exists")
            continue
        tasks.append({
            "type": "transition",
            "name": ct["name"],
            "text": ct["text"],
            "filename": filename,
            "filepath": filepath,
        })

    print(f"\nTotal tasks: {len(tasks)}")
    print(f"  Products: {sum(1 for t in tasks if t['type'] == 'product')}")
    print(f"  Transitions: {sum(1 for t in tasks if t['type'] == 'transition')}")
    print()

    if "--dry-run" in sys.argv:
        for t in tasks:
            print(f"  Would generate: {t['filename']}")
        return

    success = 0
    failed = 0
    for i, task in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {task['filename']} ...", end=" ", flush=True)
        try:
            ws._synthesize_minimax_to_path(
                task["text"],
                voice_id=voice_id,
                final_path=task["filepath"],
                speed=1.2,
            )
            print("OK")
            success += 1
        except Exception as e:
            print(f"FAILED: {e}")
            failed += 1
        if i < len(tasks):
            time.sleep(0.5)

    print(f"\nDone: {success} success, {failed} failed")


if __name__ == "__main__":
    main()
