import sqlite3
db = sqlite3.connect(r'G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db')
db.row_factory = sqlite3.Row
cur = db.cursor()
rows = cur.execute('''
SELECT b.owner_uid, b.block_label, a.path, a.status, a.text_hash AS asset_hash, b.text_hash AS block_hash
FROM script_blocks b
JOIN asset_bindings a ON a.script_block_id=b.id
WHERE b.project_id=(SELECT id FROM projects WHERE name LIKE '%键盘%')
  AND b.owner_uid IS NOT NULL
  AND a.asset_type='voice'
  AND a.account_label='小博'
  AND a.status IN ('ready','expired')
  AND b.id IN (
    SELECT script_block_id FROM asset_bindings
    WHERE asset_type='voice' AND account_label='小博' AND status IN ('ready','expired')
    GROUP BY script_block_id HAVING COUNT(DISTINCT text_hash) > 1
  )
ORDER BY b.owner_uid, b.block_label, a.text_hash
''').fetchall()
print('--- 多个不同 hash 的区块 ---')
for r in rows:
    print(f'uid={r["owner_uid"]:8s} {r["block_label"]:6s} status={r["status"]:8s} path={r["path"]}')

print()
print('--- 过期列表(过期文案)的 4 个 ---')
expired_voice = cur.execute('''
SELECT b.owner_uid, b.block_label, COUNT(*) AS c
FROM script_blocks b
JOIN asset_bindings a ON a.script_block_id=b.id
WHERE b.project_id=(SELECT id FROM projects WHERE name LIKE '%键盘%')
  AND b.owner_uid IS NOT NULL
  AND a.asset_type='voice'
  AND a.account_label='小博'
GROUP BY b.id
HAVING SUM(CASE WHEN a.status='ready' AND a.text_hash=b.text_hash THEN 1 ELSE 0 END)=0
   AND SUM(CASE WHEN a.status='ready' AND a.text_hash<>b.text_hash THEN 1 ELSE 0 END)>0
''').fetchall()
for r in expired_voice:
    print(f'  uid={r["owner_uid"]} {r["block_label"]}')
