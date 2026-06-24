import sqlite3
c=sqlite3.connect(r'G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db')
for r in c.execute("SELECT id,label,voice_id,minimax_voice_id,voice_name FROM accounts"):
    print(r)
