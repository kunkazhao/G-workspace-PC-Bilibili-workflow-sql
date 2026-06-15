# B-Workflow SQL 运维手册

## 常用操作速查

| 操作 | 入口 | 关键规则 | 验证 |
|---|---|---|---|
| 更换用户音色 | `scripts/swap_voice.py` | 先改 CONFIG 区；IndexTTS 更新 `voice_profiles` 和 `G:\Tools\IndexTTS2.0\outputs\voices\voices.json`；MiniMax 必须克隆到一个新的 `NEW_MINIMAX_VOICE_ID`，旧 voice id 不能覆盖。 | 运行脚本后确认输出 `VERIFY_DB_JSON_OK=1` 和 `SWAP_DONE=1`。 |
| MiniMax 小歪音色 | `accounts.minimax_voice_id`、`bworkflow_sql/workflow_service.py`、`C:\Users\zhaoer\.codex\skills\minimax-tts\scripts\t2a_core.py` | 当前小歪 MiniMax voice id 是 `xiaowai-v6`。App 优先读账号行的 `minimax_voice_id`；中文别名也要同步，避免单独调用 MiniMax skill 时走旧音色。 | `python scripts/_check_accounts.py`，小歪应显示 `xiaowai-v6`。 |
| IndexTTS 小歪音色 | `data\bworkflow.db.voice_profiles`、IndexTTS `voices.json` | 当前小歪参考音频是 `G:\Tools\自己用的音色\小歪10秒新.mp3`。更换时必须同步 DB 和 `voices.json` 指纹。 | `python scripts/_check_xiaowai.py`，路径应指向新参考音频。 |
| 结尾配音 | `accounts.closing_audio_path` | 生成口播 manifest 时 `_closing_manifest_entry(...)` 读取当前用户 `closing_audio_path`；文件存在才写入结尾音频。当前小歪结尾配音是 `G:\2026项目-b站\素材-配音\公共-结尾\小歪\结尾-小歪.mp3`。 | 查询 `SELECT label, closing_audio_path FROM accounts WHERE label='小歪'`；运行 `python -m pytest -q tests/test_workflow_service.py -k closing`。 |
| 弹窗居中 | `bworkflow_sql/ui.py::_center_dialog` | 所有 `CTkToplevel` 应调用 `_center_dialog(dialog)`；该函数优先按父窗口/主窗口居中，父窗口几何不可用时才按屏幕居中。不要在新弹窗里手写 `winfo_screenwidth()` 居中。 | `python -m py_compile bworkflow_sql\ui.py` 和 `python -m pytest -q tests/test_ui_helpers.py`。 |
| 字幕语义断行 | `bworkflow_sql/workflow_service.py::split_subtitle_text` | 长分句二次切分时保留数字+单位、英文型号、小数和“的/地/得”结构，优先在“但是/而且/所以”等连词前断。 | `python -m pytest -q tests/test_workflow_service.py -k subtitle`。 |

## MiniMax 换音色流程

| 步骤 | 说明 |
|---|---|
| 1. 准备参考音频 | 支持 `mp3` / `m4a` / `wav`；建议 10 秒到 5 分钟且小于 20MB。中文路径在 Python 脚本内部处理。 |
| 2. 编辑脚本配置 | 修改 `scripts/swap_voice.py` 的 `ACCOUNT_LABEL`、`INDEXTTS_VOICE_ID`、`NEW_AUDIO`、`NEW_MINIMAX_VOICE_ID`、`OLD_MINIMAX_VOICE_ID`。 |
| 3. 运行脚本 | `G:/Tools/IndexTTS2.0/wzf310/python.exe -X utf8 scripts/swap_voice.py`。 |
| 4. 同步别名 | 脚本会同步 `workflow_service.py`；如果独立 MiniMax skill 的中文别名没有同步，手动检查 `t2a_core.py`。 |
| 5. 自检 | 必须看到 `SWAP_DONE=1`。如失败，先看 `MINIMAX_REASON`，不要重复占用同一个 MiniMax voice id。 |

## 验证命令

| 场景 | 命令 |
|---|---|
| 最小 UI 回归 | `python -m pytest -q tests/test_ui_helpers.py` |
| 结尾配音回归 | `python -m pytest -q tests/test_workflow_service.py -k closing` |
| 字幕断行回归 | `python -m pytest -q tests/test_workflow_service.py -k subtitle` |
| 常用服务回归 | `python -m pytest -q tests/test_workflow_service.py tests/test_ui_helpers.py tests/test_repositories.py tests/test_sync_service.py` |

