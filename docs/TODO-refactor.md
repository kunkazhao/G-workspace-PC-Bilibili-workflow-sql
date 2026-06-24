# B-Workflow-SQL 重构 TODO

> 最后更新：2026-06-24

## 第一阶段：基础设施

### 1. DB 迁移版本化
- [x] 1.1 新建 `schema_version` 表，记录当前版本号
- [x] 1.2 把现有 `_migrate()` 里的逐列检测改写成编号迁移函数（`_migrate_v1`）
- [x] 1.3 迁移按版本号顺序执行，已执行的跳过（`_run_migrations`）
- [x] 1.4 首次启动对已有库自动检测并标记为当前版本
- [x] 1.5 补测试：空库初始化、旧库升级、重复执行幂等（3 个新测试）

### 2. SQLite 写锁
- [x] 2.1 `Database.__init__` 启用 WAL 模式
- [x] 2.2 改成单例连接 + threading.Lock 保护
- [x] 2.3 全量测试通过（117 passed）

### 3. HTTP 健壮性
- [x] 3.1 `requirements.txt` 加 `requests`
- [x] 3.2 `JsonHttpClient` 从 `urllib.request` 换成 `requests.Session` + Retry + timeout
- [x] 3.3 IndexTTS 本地调用走 `JsonHttpClient`（已有 timeout）
- [x] 3.4 Master API 健康检查改用 `requests.get` + timeout

## 第二阶段：代码拆分

### 4. ui.py 拆分
- [x] 4.1 新建 `bworkflow_sql/pages/` 包（13 个页面文件）
- [x] 4.2 拆出 `project_page.py`
- [x] 4.3 拆出 `sync_page.py`
- [x] 4.4 拆出 `voice_page.py`、`standalone_voice_page.py`
- [x] 4.5 拆出 `copy_page.py`
- [x] 4.6 拆出 `jianying_page.py`
- [x] 4.7 拆出 `account_page.py`
- [x] 4.8 拆出 `workflow_page.py`（基类）、`asset_page.py`、`subtitle_srt_page.py`、`cutme_page.py`、`rollb_rename_page.py`、`assemble_page.py`
- [x] 4.9 `ui.py` 只保留 App 主窗口 + 导航 + 页面注册（346 行）
- [x] 4.10 跑全量测试确认无回归（117 passed）

### 5. 弹窗逻辑拆分
- [x] 5.1 新建 `bworkflow_sql/dialogs/` 包
- [x] 5.2 拆出 `task_progress.py`（TaskProgressDialog）

### 7. workflow_service 拆分
- [x] 7.1 拆出 `tts_helpers.py`（491 行：TTS 常量 + 语音提供商 + 静音处理 + Markdown 转换）
- [x] 7.2 拆出 `draft_helpers.py`（108 行：剪映草稿格式化）
- [x] 7.3 拆出 `subtitle_helpers.py`（465 行：字幕断行 + ASR 对齐 + SRT 格式化）
- [x] 7.4 `workflow_service.py` 保留 WorkflowService 类 + 编排（1985 行，从 2939 行降 32%）
- [x] 7.5 更新测试 import，跑回归（117 passed）

## 第三阶段：解耦旧依赖

### 8. 去旧项目运行时依赖
- [x] 8.1 梳理 `try_import("core.master_schemes")` 实际调用（旧模块只是 HTTP 客户端调 localhost:8000）
- [x] 8.2 确认 Master HTTP API 已覆盖（/api/workspaces, /api/sourcing/categories, /api/schemes, /api/schemes/{id}/summary）
- [x] 8.3 `master_data.py` 改用 `requests` 直连 Master HTTP API，含内存缓存、重试、workspace header
- [x] 8.4 `sync_service.py` 改用 `MasterDataService.fetch_scheme_summary()`，删除 legacy_bridge 导入
- [x] 8.5 删除 `legacy_bridge.py`（`install_legacy_paths()` / `try_import()` 已无调用方）
- [x] 8.6 `LEGACY_PROJECT_ROOT` 保留（`legacy_import.py` 数据迁移仍需要）；`legacy_import.py` 保留（一次性导入旧数据用）

## 第四阶段：UI 体验提升

### 9. 异步任务体验
- [x] 9.2 进度条：`TaskProgressDialog` 已有 indeterminate 进度条
- [x] 9.3 取消按钮：TaskProgressDialog 加取消按钮 + `cancel_event`（threading.Event），配音循环每轮检查并 break，已生成文件保留（118 passed）
- [x] 9.4 任务完成汇总：`TaskProgressDialog.finish()` 已支持 headline + message + detail

## 第五阶段：项目卫生

### 10. requirements.txt 补全
- [x] 10.1 补 `customtkinter`、`requests`、`sv-ttk`

### 11. 根目录临时文件清理
- [x] 11.1 `.gitignore` 加临时文件规则（`_tmp_*.py`、`cutme_launch_err.txt`）
- [x] 11.2 已有临时文件清理（`_tmp_*.py` 已被 gitignore，磁盘保留不删）
- [x] 11.3 `__pycache__/` 已在 `.gitignore`

### 12. scripts/ 目录整理
- [x] 12.1 正式工具留 `scripts/`（`swap_voice.py`、`batch_tts_*.py`、`gen_manifest_*.py`、`subtitle_asr_worker.py`）
- [x] 12.2 调试脚本移到 `scripts/debug/`（`_check_*.py`、`fetch_tags.py`）
- [x] 12.3 一次性工具移到 `scripts/oneoff/`（`convert_app_icon.py`、`write_review_report.py`）

## 遗留问题
（执行中遇到的未解决问题记录在此）
