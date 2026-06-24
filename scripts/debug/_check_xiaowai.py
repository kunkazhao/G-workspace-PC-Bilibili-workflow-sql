# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect(r'G:\workspace\PC-Bilibili-workflow-sql\data\bworkflow.db')
print("voice_profiles columns:")
for r in c.execute("PRAGMA table_info(voice_profiles)"):
    print(" ", r[1], r[2])
print()
print("rows where name/voice_id/display_name == 小歪:")
for r in c.execute("SELECT * FROM voice_profiles WHERE voice_id='小歪' OR display_name='小歪'"):
    print(r)
