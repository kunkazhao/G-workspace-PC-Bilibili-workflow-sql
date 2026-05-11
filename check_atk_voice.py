"""检查 ATK RS7Air 配音过期情况，双击运行即可。"""
import sqlite3, sys
from pathlib import Path

db_path = Path(r"G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db")
if not db_path.exists():
    input(f"数据库不存在：{db_path}\n按回车退出...")
    sys.exit(1)

db = sqlite3.connect(str(db_path))
db.row_factory = sqlite3.Row

# 固定查询 JP076 ATK RS7Air（键盘）
uid = "JP076"

# 1. 查商品
cur = db.execute("""
    SELECT p.id, p.uid, p.title, p.price_label, pr.name as project_name, pr.id as project_id
    FROM products p JOIN projects pr ON pr.id = p.project_id
    WHERE p.uid = ?
""", (uid,))
product = cur.fetchone()
if not product:
    print(f"未找到 {uid}")
    db.close()
    input("按回车退出...")
    sys.exit(1)

pid = product["project_id"]
print(f"商品：{product['uid']} {product['title']} ({product['price_label']})")
print(f"项目：{product['project_name']} (ID={pid})")
print()

# 2. 查该商品所有配音记录
print("=" * 60)
print(f"【{uid} 所有配音记录】")
print("=" * 60)
cur = db.execute("""
    SELECT id, account_label, status, text_hash, path
    FROM asset_bindings
    WHERE project_id = ? AND uid = ? AND asset_type = 'voice'
    ORDER BY account_label, id
""", (pid, uid))
voices = [dict(r) for r in cur.fetchall()]
if not voices:
    print("（无配音记录）")
else:
    for v in voices:
        print(f"ID={v['id']}")
        print(f"  用户：{v['account_label'] or '(空)'}")
        print(f"  状态：{v['status']}")
        print(f"  hash：{v['text_hash'] or '(空)'}")
        print(f"  路径：{v['path']}")
        print()

# 3. 查当前文案 text_hash
print("=" * 60)
print(f"【{uid} 当前文案 text_hash】")
print("=" * 60)
cur = db.execute("""
    SELECT id, block_label, body, text_hash
    FROM script_blocks
    WHERE project_id = ? AND owner_uid = ? AND active = 1
""", (pid, uid))
blocks = [dict(r) for r in cur.fetchall()]
if not blocks:
    print("（无当前文案块）")
    current_hashes = set()
else:
    for b in blocks:
        print(f"  block_id={b['id']} label={b['block_label']}")
        print(f"  hash={b['text_hash']}")
        print(f"  正文开头：{b['body'][:50]}...")
        print()
    current_hashes = {b['text_hash'] for b in blocks}

# 4. 对比分析
print("=" * 60)
print("【对比分析】")
print("=" * 60)
print(f"当前文案 hash 集合：{current_hashes}")
print()

for v in voices:
    label = v['account_label'] or '(空)'
    vhash = v['text_hash'] or ''
    status = v['status']

    if status != 'ready':
        print(f"  {label} 配音 (ID={v['id']}) 状态={status} → 不是 ready，不会被检查")
        continue

    if vhash in current_hashes:
        print(f"  ✅ {label} 配音 (ID={v['id']}) hash 匹配当前文案 → ready")
    else:
        print(f"  ❌ {label} 配音 (ID={v['id']}) hash 不匹配当前文案 → 资产中心显示「配音过期」")
        print(f"     配音 hash：{vhash}")
        print(f"     文案 hash：{current_hashes}")

print()
print("说明：")
print("- 资产中心判断逻辑：检查该商品+该用户下，是否存在 ready 配音且 hash 不在当前文案 hash 集合中")
print("- 口播稿预检判断逻辑：使用 voice_state()，该函数在匹配用户时还会匹配无标签(空)的配音")
if not voices:
    print("- 无配音记录，请先生成配音")

db.close()
input("\n按回车退出...")
