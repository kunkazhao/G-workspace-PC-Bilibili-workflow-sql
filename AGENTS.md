这个文件夹是我的 B 站工作流的项目
语气:直接、专业，不闲聊
以表格形式保存可交付成果和时间表

## 中文路径与脚本执行规范

1. 禁止使用 `@' ... '@ | python -` 这类内联 Python 管道方式处理包含中文路径的任务。
2. Python 逻辑必须落到真实 `.py` 文件中执行，不要把业务逻辑塞进 shell here-string。
3. PowerShell 只负责启动脚本；优先传 ASCII 参数、环境变量或配置文件路径，不要在 shell 里拼接大段中文文本。
4. 中文路径优先由 Python 内部处理，统一使用 `pathlib.Path`、`Path.glob()`、`Path.cwd()` 等方式完成定位和遍历。
5. 批处理目录、文件枚举、配置读取等涉及中文路径的逻辑，优先放在 Python 脚本内部完成，不要让 shell 负责路径展开。
6. `.py`、`.ps1`、`.json`、`.md`、`.txt` 统一保存为 UTF-8。
7. 控制台编码设置只能作为辅助，不作为根治方案；长期方案是避开 stdin / here-string / 管道传中文路径这类高风险链路。

## 当前实现速查

| 范围 | 规则 |
|---|---|
| 项目下拉框 | 页面只显示项目中文名称，不显示数据库 id；内部通过 `App._project_selector_id_by_value` 回查 id。项目列表按 `Repository.projects()` 的名称升序排序。 |
| 项目重名 | `ProjectPageDialog` 保存前用 `project_name_exists(...)` 校验，重名时提示用户，不创建重复项目。 |
| Master 同步 | 同步中心先预览 Master 变化；如果 Master API 连接失败，用 `MasterServiceManager.ensure_running()` 尝试启动 `G:\workspace\bilibili-newTools-next-master` 后端再重试预览。 |
| 手动配音映射 | 同步中心的配音检查对缺失和过期配音提供 `手动映射音频`，调用 `SyncService.manual_bind_voice_asset(...)` 写入 `asset_bindings.source_kind='manual'`，并用当前文案 hash 标记 ready。 |
| 配音架构 | 项目配音通过 `TtsProviderRegistry` 调度 `IndexTtsProvider` / `MiniMaxTtsProvider`；业务循环只依赖统一 request/result 契约。CLI `voice` 与 `voice-counts` 都接受 `--voice-provider minimax|indextts`，默认 `minimax`，两步必须使用同一 provider。 |
| 配音配置 | `account_voice_profiles(account_id, provider, voice_id, model, settings_json, enabled)` 是 provider 配置正本；`accounts.voice_id` 和 `accounts.minimax_voice_id` 只作迁移兼容。生成复用必须同时匹配账号、文本 hash、provider、model、voice ID 和合成设置。 |
| MiniMax 配置 | API key 读取顺序是环境变量 `MINIMAX_API_KEY`，然后 `C:\Users\zhaoer\.codex\skills\zhaoer-tools-minimax-tts\.env`，再兼容旧路径 `C:\Users\zhaoer\.codex\skills\minimax-tts\.env` 和当前工作目录 `.env`。常用映射：小博 `xiaobo-v2`，小燃 `xiaoran-v2`，小歪 `xiaowai-v6`，知了 `bilibili-zhiliao`，荣荣/蓉蓉 `rongrong-v2`。 |
| MiniMax 换音色 | 用 `scripts/swap_voice.py`；MiniMax 旧 voice id 不能覆盖，必须克隆到新的 `NEW_MINIMAX_VOICE_ID`。脚本更新 `account_voice_profiles` 和兼容字段，禁止字符串替换应用或外部 Skill 源码；成功时输出 `SWAP_DONE=1`。 |
| IndexTTS 音色 | 本地 voice profile 的 `speaker_audio_path` 是重新注册 IndexTTS 的来源路径。更换参考音频时要同步 `data\bworkflow.db.voice_profiles`，不要只改 `G:\Tools\IndexTTS2.0\outputs\voices\voices.json`。 |
| 小歪当前音色 | IndexTTS 参考音频：`G:\Tools\自己用的音色\小歪10秒新.mp3`；MiniMax voice id：`xiaowai-v6`。 |
| 小歪结尾配音 | `accounts.closing_audio_path` 当前为 `G:\2026项目-b站\素材-配音\公共-结尾\小歪\结尾-小歪.mp3`；生成草稿时 `_closing_manifest_entry(...)` 只在文件存在时写入结尾音频。 |
| 弹窗居中 | 新建 `CTkToplevel` 后统一调用 `_center_dialog(dialog)`；该函数按父窗口/主窗口居中，只有父窗口几何不可用时才兜底按屏幕居中。不要新写 `winfo_screenwidth()` 居中逻辑。 |
| 模板视频位置校准 | 商品视频位置、`display_video_slot`、封面区域对齐、新增/修改商品图模板、剪映坐标/X/Y/缩放问题，先用 `zhaoer-flow-templatepreset` skill；再运行 `python -m bworkflow_sql template-calibrate <project_id> --account <账号> --product-uid <UID> --draft-name 模板校准-<账号>-<UID>` 生成单商品校准草稿，不要跑整批草稿调一个位置。 |
| 字幕断行 | 统一维护在 `bworkflow_sql/subtitle_rules.py::split_subtitle_text(...)`；SRT 导出和剪映文本字幕轨都复用它。对超长分句做语义断行，保留数字+单位、英文型号、小数和 `的/地/得` 结构，优先在连词前断。 |
| 完整 MP4 | `render-final-video` 默认 ASR，生成一个包含无字幕引言、价格/商品段和账号固定结尾的 RenderPackage；一次 CutMe 渲染、一份全局 ASS、一个完整 MP4。不要恢复 B-Workflow 外层引言 concat 或单独商品段交付。 |
| 媒体工作区 | `create-project` 自动为所有启用账号创建配音、实际配置模板商品图、Roll-B 和引言展示视频目录；旧项目用 `scaffold` 幂等修复，不手工复制目录逻辑。 |
| 正式成片履历 | run manifest 只证明生成过。只有用户确认后执行 `confirm-production` 才写入 SQLite `production_runs`；测试、预览、校准不计入。下次选模板先查 `production-history`。 |
| 发布完成与归档 | 复用 `production_runs` 和 `.pipeline.json` 的 `phases.publishing`。`complete-publishing` 默认移动到 `G:\2026项目-b站\已发布视频` 下已存在的当前月份目录，不存在则放根目录且不建月份目录；`--archive-dir` 可覆盖，`--current-path` 校验手工移动后的文件。 |
| 蓝链回流 | 发布归档后进入 `phases.blue_link_backfill`，不直接结束。`publishing-context` 只通过正式成片的本地 `account_id` 读取固定 Master UUID/B站 MID/方案 ID；`resolve-blue-links` 是单链接诊断，`resolve-blue-link-backfill <backfill_id> --workspace-id <uuid>` 只拉 Master 当前授权的 `browser_pending`，每条只尝试一次并立即回写成功或失败。京东请求默认限速，明确 403 后持久熔断两小时且不阻塞淘宝；库内缺商品、旧链冲突、延后和安全挂起行不会重复打开。`record-blue-link-backfill` 必须读取 Master 快照核验身份和计数后再写同一 `production_runs` 行。 |
| 视频组件与剪辑模板 | 进入 `zhaoer-bilibili-video-design`，按“静态预览确认 -> 动画短样片确认 -> 正式组件接入”推进。禁止把视频画面做成网页/HUD，禁止为视觉方便改写结构化槽位；正式随机必须记录稳定 id 和 seed。 |
| 手动测试目录 | 预览、短样片、RenderPackage、ASS、抽帧和测试缓存统一放在 `data\workspace\manual-tests\{测试主题}\`，每个主题独立目录；默认不写正式 `.pipeline.json`，CutMe 临时 job 验证后只清理该次明确生成的目录。 |
| 剪映字幕轨 | `bworkflow_sql jianying` 默认仍跳过字幕；显式加 `--with-subtitles` 才生成可编辑文本轨。当前机器如遇 onnxruntime/VAD 初始化失败，再加 `--subtitle-no-vad`。 |
| 验证命令 | 从仓库根目录运行 `python -m pytest`，不要用裸 `pytest`。最小回归常用：`python -m pytest -q tests/test_workflow_service.py tests/test_ui_helpers.py tests/test_repositories.py tests/test_sync_service.py`。 |
