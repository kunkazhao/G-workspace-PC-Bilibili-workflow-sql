import sqlite3
db = sqlite3.connect(r'G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db')
db.row_factory = sqlite3.Row
cur = db.cursor()

# 备份
backup = cur.execute('''
SELECT b.id AS script_block_id, b.owner_uid, b.block_label, a.path, a.text_hash, a.status
FROM script_blocks b
JOIN asset_bindings a ON a.script_block_id=b.id
WHERE b.project_id=(SELECT id FROM projects WHERE name LIKE '%键盘%')
  AND a.asset_type='voice' AND a.account_label='小博'
  AND a.status='expired'
''').fetchall()
print('--- 待删除的 expired 旧记录 ---')
for r in backup:
    print(f'  uid={r["owner_uid"]:8s} {r["block_label"]:6s} path={r["path"]}')

# 软删除:把 status 改成 'stale' 保留审计
n = cur.execute('''
UPDATE asset_bindings SET status='stale', updated_at=datetime('now')
WHERE asset_type='voice' AND account_label='小博' AND status='expired'
  AND script_block_id IN (
    SELECT id FROM script_blocks
    WHERE project_id=(SELECT id FROM projects WHERE name LIKE '%键盘%')
  )
''').rowcount
print(f'\n已标记 stale 数量: {n}')
db.commit()

# 验证
ready_count = cur.execute('''
SELECT COUNT(*) FROM asset_bindings a
JOIN script_blocks b ON b.id=a.script_block_id
WHERE b.project_id=(SELECT id FROM projects WHERE name LIKE '%键盘%')
  AND a.asset_type='voice' AND a.account_label='小博'
  AND a.status='ready' AND a.text_hash=b.text_hash
''').fetchone()[0]
expired_count = cur.execute('''
SELECT COUNT(*) FROM asset_bindings
WHERE asset_type='voice' AND account_label='小博' AND status='expired'
''').fetchone()[0]
print(f'\n小博 ready 且 hash 匹配: {ready_count}')
print(f'剩余 expired 记录: {expired_count}')
