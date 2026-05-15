"""检查鼠标项目下的商品和同步状态"""
import sqlite3
from pathlib import Path

db_path = Path(r"G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db")
if not db_path.exists():
    input(f"数据库不存在：{db_path}\n按回车退出...")
    exit()

db = sqlite3.connect(str(db_path))
db.row_factory = sqlite3.Row

# 找鼠标项目
cur = db.execute("SELECT id, name, scheme_id, scheme_name FROM projects WHERE name LIKE '%鼠标%'")
proj = cur.fetchone()
if not proj:
    print("未找到鼠标项目")
    db.close()
    input("按回车退出...")
    exit()

print(f"项目：{proj['name']} (ID={proj['id']})")
print(f"方案：{proj['scheme_name']} (ID={proj['scheme_id']})")
print()

# 查所有商品
cur = db.execute("""
    SELECT uid, title, price_label, removed_from_master
    FROM products
    WHERE project_id = ?
    ORDER BY id
""", (proj['id'],))
products = cur.fetchall()
print(f"商品总数：{len(products)}")
print()
for p in products:
    rm = " [已移除]" if p['removed_from_master'] else ""
    print(f"  {p['uid']:8s} {str(p['title'] or ''):25s} {str(p['price_label'] or ''):10s}{rm}")

# 查 script_blocks 看 SB080 和 SB081 有没有
print()
cur = db.execute("""
    SELECT sb.owner_uid, sb.block_label, sb.script_id, sb.body
    FROM script_blocks sb
    WHERE sb.project_id = ? AND sb.owner_uid IN ('SB080', 'SB081') AND sb.active = 1
""", (proj['id'],))
blocks = cur.fetchall()
if blocks:
    print("SB080/SB081 在 script_blocks 中的记录：")
    for b in blocks:
        print(f"  UID={b['owner_uid']} label={b['block_label']} script_id={b['script_id']}")
        print(f"  正文开头：{str(b['body'])[:40]}...")
        print()
else:
    print("SB080/SB081 在 script_blocks 中无记录")
    print("→ 说明同步 MD 时没能把文案块入库")

# 查最近同步事件
print()
cur = db.execute("""
    SELECT event_type, status, message, created_at
    FROM sync_events
    WHERE project_id = ?
    ORDER BY id DESC LIMIT 5
""", (proj['id'],))
events = cur.fetchall()
print("最近同步记录：")
for e in events:
    print(f"  {e['event_type']:20s} {e['status']:10s} {str(e['message'])[:60]}")

db.close()
input("\n按回车退出...")
