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

## 非价格过渡口播稿（品类过渡 / 自定义分组过渡）

软件的口播稿流程默认按价格段切分商品（`## 价格过渡文案`）。当品类需要按用途、功能或自定义标签分组时（如充电宝按"高性价比款/日常款/小巧精致款/磁吸便捷款/高性能款"），软件无法直接生成，需要手动走以下流程。

### 前置条件

| 条件 | 说明 |
|---|---|
| MD 文案 | 引言、商品、过渡文案都写好。过渡文案的二级标题可以不叫"价格过渡文案"（如 `## 品类过渡文案`），三级标题是分组名（如 `### 高性价比款`） |
| Master 标签 | 在 Master 方案的商品上打好标签（tag），标签值 = 过渡文案里的分组名 |
| 图片同步 | 商品图片已同步到对应用户 + 模板目录 |
| 配音目录 | 确认配音输出路径 |

### 步骤总览

| 步骤 | 脚本 | 说明 |
|---|---|---|
| 1. 获取标签分组 | 手动查 Master API | scheme summary 的 `tags` 字段为空是已知 bug（`SCHEME_SUMMARY_ITEM_SELECT` 没有 `tags`）；必须逐个查 `/api/sourcing/items/{source_id}` 才能拿到 `tags` |
| 2. 批量 MiniMax 配音 | `scripts/batch_tts_chongdianbao.py` | 为商品正文 + 过渡文案调用 MiniMax T2A |
| 3. 生成 manifest | `scripts/gen_manifest_chongdianbao.py` | 按标签分组组织 entry 顺序：引言 → [过渡 → 该分组商品] × N → 结尾 |
| 4. 生成剪映草稿 | `scripts/jianying_engine/generate_jianying_draft.py` | 项目内剪映引擎优先；`BWORKFLOW_JIANYING_ENGINE_DIR` 可覆盖；旧 b-workflow skill 只作迁移兜底 |

### 步骤详解

#### 1. 获取标签分组

Master API `GET /api/schemes/{scheme_id}/summary` 返回的 items 里 `tags` 字段为空，这是因为 `backend/api/schemes.py` 的 `SCHEME_SUMMARY_ITEM_SELECT` 没有包含 `tags`。

**解决方法**：先从 summary 拿到每个 item 的 `source_id`，再逐个调 `GET /api/sourcing/items/{source_id}` 获取 `tags`。

**注意**：Master 的 HTTP 头 `X-Workspace-Id` 需要传中文（"赵二"），Python 的 `http.client` 会因 latin-1 编码报错。解决方案：
- 用 `socket` 直接发 UTF-8 raw HTTP 请求
- 或把查询逻辑写进 `.py` 文件执行（避免 shell 管道编码问题）

工作空间 UUID 映射（可通过 `GET /api/workspaces` 获取）：

| 名称 | UUID |
|---|---|
| 赵二 | `de90965d-29e4-4ac3-9730-0ce1fc85b67c` |

#### 2. 批量 MiniMax 配音

参考脚本 `scripts/batch_tts_chongdianbao.py`，核心逻辑：

- 从 DB 读 `script_blocks` 获取商品文案正文
- 过渡文案文本硬编码在脚本里（因为 MD parser 只识别 `## 价格过渡文案`，自定义标题不入库）
- 调用 `WorkflowService._synthesize_minimax_to_path()` 生成音频
- 文件命名：商品 `{price}-{uid}-{title}-正文.mp3`，过渡 `0-品类过渡-{分组名}.mp3`
- 速度 1.2，voice_id 从 `accounts.minimax_voice_id` 取

**复用要点**：新品类只需改脚本里的 `project_id`、`voice_id`、`output_dir`、`category_transitions` 列表。

#### 3. 生成 manifest

参考脚本 `scripts/gen_manifest_chongdianbao.py`，核心结构：

```
manifest.entries 顺序：
  1. intro（section="intro", type="transition"）
  2. 循环每个分组：
     a. 品类过渡（section="price_transition", type="transition"）
     b. 该分组下的商品（section="product", type="product"）
  3. closing（section="closing", type="closing"）
```

**关键字段**：
- `display_template`：模板名（如 `荣荣-模板2`）
- `display_video_slot`：从 `template_config.py` 取坐标
- 过渡 entry 的 `price_range_label` 填分组名，剪映草稿生成器会用它生成标题卡
- `product_uid` 对于过渡用 `CATEGORY_TRANSITION`（不影响草稿生成，只是标识）
- 音频和图片路径必须是绝对路径

#### 4. 生成剪映草稿

```bash
python_exe="G:/workspace/PC-Bilibili-workflow-sql/scripts/jianying_engine/.venv/Scripts/python.exe"
script="G:/workspace/PC-Bilibili-workflow-sql/scripts/jianying_engine/generate_jianying_draft.py"

"$python_exe" "$script" \
  --manifest "<manifest_path>" \
  --draft-name "<草稿名>" \
  --draft-root "E:/剪辑-剪映/草稿/JianyingPro Drafts" \
  --allow-replace \
  --skip-subtitles
```

如果项目内 `.venv` 还没初始化，先执行：

```bash
python -m venv "G:/workspace/PC-Bilibili-workflow-sql/scripts/jianying_engine/.venv"
"G:/workspace/PC-Bilibili-workflow-sql/scripts/jianying_engine/.venv/Scripts/python.exe" -m pip install -r "G:/workspace/PC-Bilibili-workflow-sql/scripts/jianying_engine/requirements-jianying.txt"
```

### 实际案例：充电宝品类（2026-06-20）

| 项目 | 值 |
|---|---|
| project_id | 14 |
| 项目名 | 数码-充电宝 |
| 用户 | 荣荣 |
| 模板 | 荣荣-模板2 |
| 配音 | MiniMax rongrong-v2 |
| 分组方式 | 5 个品类标签（非价格段） |
| 商品数 | 26 |
| 配音数 | 31（26 商品 + 5 品类过渡） |
| 引言 | 1 条（已有） |
| 结尾 | accounts.closing_audio_path（荣荣） |
| 总时长 | 1079.8 秒（约 18 分钟） |
| manifest | `data/manifests/数码-充电宝-荣荣-品类过渡.manifest.json` |
| 草稿目录 | `E:\剪辑-剪映\草稿\JianyingPro Drafts\数码-充电宝-荣荣-品类过渡` |

### 踩坑记录

| 问题 | 原因 | 解决 |
|---|---|---|
| Master API 拿不到 tags | `SCHEME_SUMMARY_ITEM_SELECT` 没有 `tags` 列 | 逐个查 `/api/sourcing/items/{source_id}` |
| `X-Workspace-Id` 中文报错 | Python `http.client` 强制 latin-1 编码 | 用 raw socket 发 UTF-8 请求 |
| 品类过渡文案不入库 | MD parser 只识别 `## 价格过渡文案` | 过渡文本硬编码在脚本里 |
| shell 环境中文乱码 | Git Bash 的 stdin 编码 | 业务逻辑写 `.py` 文件，不走 shell 管道 |

## 验证命令

| 场景 | 命令 |
|---|---|
| 最小 UI 回归 | `python -m pytest -q tests/test_ui_helpers.py` |
| 结尾配音回归 | `python -m pytest -q tests/test_workflow_service.py -k closing` |
| 字幕断行回归 | `python -m pytest -q tests/test_workflow_service.py -k subtitle` |
| 引言场景 ASR 对齐回归 | `python -m pytest -q tests/test_cutme_intro.py tests/test_intro_timeline.py` |
| 常用服务回归 | `python -m pytest -q tests/test_workflow_service.py tests/test_ui_helpers.py tests/test_repositories.py tests/test_sync_service.py` |

## CutMe 引言场景时间轴

`bworkflow_sql.intro_timeline.align_intro_plan_scenes_with_asr(...)` 负责把 CutMe 的
`intro_plan.scenes[].text` 和整段引言配音做 ASR 对齐，输出 `scenes[].timing`。

关键规则：

- 对齐前必须校验 `scenes[].text` 拼接后与 `full_script` 一致，不能让 LLM 改字后继续对齐。
- ASR 仍复用现有独立子进程和 `align_subtitle_text_with_units(...)`，不要在 CutMe 里重复引入 Whisper。
- CutMe 只消费 `scenes[].timing`，并根据 `hook_open`、`pain_points`、`self_check`、`priority_preview` 控制产品展示和引导三连素材。

## CutMe 引言页面新链路

`工具 -> CutMe 引言` 现在支持两条链路：

- 选择 `引言计划 JSON`：走新链路。页面会先校验 `intro_plan.full_script` 与当前引言文案一致，再按 `G:\2026项目-b站\素材-自动剪辑\{一级品类-二级品类}` 随机选择三段不重复产品展示素材，并从 `通用` 文件夹随机选择文件名包含 `引导三连` 的视频。缺产品展示或缺引导三连时，在渲染前直接报错。
- 不选择 `引言计划 JSON`：走旧链路，继续使用 `素材文件夹 + cutme_service.generate_intro_video(...)`，不会得到 `selected_assets` 和 ASR 场景 timing。

新链路准备好的中间文件会写入：

```text
data\workspace\project-{project_id}\intro\intro-plan-{script_block_id}-{account}.json
data\workspace\project-{project_id}\intro\cutme-config-{script_block_id}-{account}.json
```

`cutme-config` 通过 `intro_plan_path` 交给 `python -m cutme` 渲染。页面日志会显示准备后的 `intro_plan`、CutMe 配置、素材预检结果、是否执行 ASR 对齐，以及最终选中的素材路径。
