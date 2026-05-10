"""手动同步"家居-体脂秤"项目的 Master 方案商品。"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bworkflow_sql.db import Database
from bworkflow_sql.repositories import Repository
from bworkflow_sql.sync_service import SyncService
from bworkflow_sql.master_data import MasterDataService
from bworkflow_sql.legacy_bridge import install_legacy_paths
from bworkflow_sql.utils import safe_text

db = Database()
repo = Repository(db)
sync = SyncService(db)
master = MasterDataService()

# 查找家居-体脂秤项目
projects = repo.projects()
target = None
for p in projects:
    if "体脂秤" in p.get("name", "") or "家居" in p.get("name", ""):
        target = p
        break

if not target:
    print("未找到家居-体脂秤项目，现有项目：")
    for p in projects:
        print(f"  ID={p['id']}  name={p['name']}")
    sys.exit(1)

print(f"找到项目：ID={target['id']}  name={target['name']}")

# 如果 workspace_id 为空，从 Master 获取默认 workspace
if not target.get("workspace_id"):
    print("workspace_id 为空，正在获取默认工作空间...")
    workspaces = master.fetch_workspaces(force_refresh=True)
    default = next((w for w in workspaces if safe_text(w.get("name")) == "赵二" or safe_text(w.get("slug")) == "zhaoer"), workspaces[0] if workspaces else None)
    if default:
        wid = safe_text(default.get("id"))
        wname = safe_text(default.get("name"))
        print(f"找到默认工作空间：{wname} (ID={wid})")
        db.execute("UPDATE projects SET workspace_id=?, workspace_name=? WHERE id=?", (wid, wname, target["id"]))
        target["workspace_id"] = wid
        target["workspace_name"] = wname
        print("已更新项目 workspace_id")
    else:
        print("无法获取默认工作空间")
        sys.exit(1)

print(f"workspace_id={target.get('workspace_id')}  scheme_id={target.get('scheme_id')}")
print()

if not target.get("scheme_id"):
    print("该项目缺少 scheme_id，无法同步。")
    print("请先在品类项目中选方案并保存。")
    sys.exit(1)

print("正在同步 Master 方案商品...")
result = sync.sync_master_scheme(target["id"], apply_changes=True)
print(f"同步完成：新增 {len(result['added'])} 个，更新 {len(result['updated'])} 个，移除 {len(result['removed'])} 个")
print()
print("现在可以在品类项目中点击创建/更新文案框架了。")
input("按回车退出...")
