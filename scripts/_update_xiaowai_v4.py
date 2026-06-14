import sqlite3
from pathlib import Path

DB = Path(r'G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db')
NEW_ID = 'xiaowai-v4'

c = sqlite3.connect(DB)
cur = c.cursor()
cur.execute("UPDATE accounts SET minimax_voice_id=? WHERE label='小歪'", (NEW_ID,))
print('accounts updated rows:', cur.rowcount)
c.commit()
for r in c.execute("SELECT label,minimax_voice_id FROM accounts WHERE label='小歪'"):
    print('  ->', r)
c.close()
