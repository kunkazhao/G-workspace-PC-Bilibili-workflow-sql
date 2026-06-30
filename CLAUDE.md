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
| 配音方式 | `生成配音` 和 `单独配音` 都支持 `IndexTTS 本地服务` 与 `MiniMax API`。页面仍按同一个用户名称选择，后端用 `voice_id` 对应 IndexTTS、`minimax_voice_id` 对应 MiniMax。 |
| MiniMax 配置 | API key 读取顺序是环境变量 `MINIMAX_API_KEY`，然后 `C:\Users\zhaoer\.codex\skills\zhaoer-tools-minimax-tts\.env`，再兼容旧路径 `C:\Users\zhaoer\.codex\skills\minimax-tts\.env` 和当前工作目录 `.env`。常用映射：小博 `xiaobo-v2`，小燃 `xiaoran-v2`，小歪 `xiaowai-v6`，知了 `bilibili-zhiliao`，荣荣/蓉蓉 `rongrong-v2`。 |
| MiniMax 换音色 | 用 `scripts/swap_voice.py`；MiniMax 旧 voice id 不能覆盖，必须克隆到新的 `NEW_MINIMAX_VOICE_ID`。脚本兼容无 `MINIMAX_GROUP_ID` 的 `.env`，成功时输出 `SWAP_DONE=1`。 |
| IndexTTS 音色 | 本地 voice profile 的 `speaker_audio_path` 是重新注册 IndexTTS 的来源路径。更换参考音频时要同步 `data\bworkflow.db.voice_profiles`，不要只改 `G:\Tools\IndexTTS2.0\outputs\voices\voices.json`。 |
| 小歪当前音色 | IndexTTS 参考音频：`G:\Tools\自己用的音色\小歪10秒新.mp3`；MiniMax voice id：`xiaowai-v6`。 |
| 小歪结尾配音 | `accounts.closing_audio_path` 当前为 `G:\2026项目-b站\素材-配音\公共-结尾\小歪\结尾-小歪.mp3`；生成草稿时 `_closing_manifest_entry(...)` 只在文件存在时写入结尾音频。 |
| 弹窗居中 | 新建 `CTkToplevel` 后统一调用 `_center_dialog(dialog)`；该函数按父窗口/主窗口居中，只有父窗口几何不可用时才兜底按屏幕居中。不要新写 `winfo_screenwidth()` 居中逻辑。 |
| 模板坐标转换 | `template_config.py` 的 x/y/width/height 是画布像素坐标（左上角原点）。转成剪映 UI 值：`剪映X = (center_x-960)×2`，`剪映Y = (540-center_y)×2`，`缩放% = display_scale×100`。注意乘除 2 不是 960/540。 |
| 字幕断行 | `split_subtitle_text(...)` 对超长分句做语义断行，保留数字+单位、英文型号、小数和 `的/地/得` 结构，优先在连词前断。 |
| 验证命令 | 从仓库根目录运行 `python -m pytest`，不要用裸 `pytest`。最小回归常用：`python -m pytest -q tests/test_workflow_service.py tests/test_ui_helpers.py tests/test_repositories.py tests/test_sync_service.py`。 |
| 非价格过渡口播稿 | 当品类按用途/标签分组而非价格段时（如充电宝按品类标签），软件无法直接生成口播稿。完整流程和踩坑记录见 `docs/operator-runbook.md` 的「非价格过渡口播稿」章节。参考脚本：`scripts/batch_tts_chongdianbao.py`（批量配音）、`scripts/gen_manifest_chongdianbao.py`（生成 manifest）。 |
