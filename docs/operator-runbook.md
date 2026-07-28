# B-Workflow SQL 运维手册

## 多任务与正式渲染占用

- 每个新任务使用自己的 schema-v2 `.pipeline.json` 和 `episode_id`；不得用
  品类名猜选任务。
- 文案、预检、配音和打包准备可以在多个对话中并行。
- `render-intro-video`、`render-final-video`、`rerender-production` 共用一个
  全局渲染占用门禁。若返回 `render_busy`，不得绕过门禁直接调用 CutMe，
  也不得自动循环重试；向用户说明当前占用任务，等完成后再让当前对话“继续”。
- 不提供自动排队，也不支持两个正式渲染同时运行。

## 动态商品卡正式生成

正式商品段只走 `动态 ProductCard -> Remotion 短 MP4 -> 完整 MP4`，不读取、
不生成整卡 PNG，也不做静态回退。先确认阶段 7，再运行：

```powershell
python -m bworkflow_sql product-card-preflight <project_id> --account <账号> --product-card-template-id <模板>
python -m bworkflow_sql render-final-video <project_id> --pipeline <.pipeline.json> --account <账号> --product-card-template-id <模板> --product-media-mode video_preferred
```

预检使用同一份冻结 Master 方案快照检查全部商品；商品名称、实际整数元价格、
唯一价格段、当前配音、商品视频或封面、模板必填槽位任何一项失败，都在
CutMe job 和缓存写入前整体阻断并返回 UID。缓存固定在
`data/workspace/project-<id>/render/final-video-cache`，不跨项目且不自动清理。

`product-images`、剪映公开 CLI/UI 和模板校准草稿已退役。历史图片、绑定、
草稿和引擎保留但不再兼容新槽位/模板。CutMe still 只用于模板压力预览和
人工验收。
## 按产物身份确认

不要通过手工修改 `status` 验收引言。用户确认后运行
`confirm-intro-video`，原子记录路径、SHA-256、大小、source plan 版本和确认时间。
完整成片继续使用 `confirm-production`；传入 `--final-path` 时，哈希绑定的是实际
确认的后期剪辑版。文件内容变化后 TotalControl 会要求重新确认。

## 媒体工作区与正式成片履历

阶段 7 开始时，先运行 `template-doctor` 展示 `media_inventory`，并读取
`workflow-doctor.checks.phase7_selection.featured_products`。没有重点商品时默认
`standard` 且不置顶；一款时展示 UID/完整名称并询问是否置顶；多款时展示全部
UID/完整名称并询问是否全部置顶。随后一次确认输出分支、账号、商品卡模板、商品素材方式和排序。
确认后运行：

```powershell
python -m bworkflow_sql confirm-phase7-selection --pipeline <.pipeline.json> --output-branch final_mp4 --account <账号> --product-card-template-id <模板> --product-media-mode cover_only|video_preferred --product-order-strategy price_segment_shuffle|stable --mode standard|top --top-uids <可选UID列表>
```

`render-package` 与 `render-final-video` 必须携带同一 pipeline 和完全一致的参数；
缺少确认、选择哈希损坏或参数不一致都会阻断。旧候选片没有匹配的
`phase7_selection_hash` 时只能作为诊断产物，不能执行 `confirm-production`。
商品卡模板还必须有版本、正式组件源码、文字承载依赖源码和容量基线 SHA-256
绑定的 `textCapacityCertification`；任一源码或基线变化后认证自动失效。

- 新建项目自动建立所有启用账号的配音目录、实际配置模板的商品图目录、品类 Roll-B 目录和引言展示视频目录。
- 旧项目或外部盘恢复后运行 `python -m bworkflow_sql scaffold <project_id>` 幂等补齐。
- `render-final-video` 只写生成证据。用户确认完整 MP4 是实际成品后，运行 `python -m bworkflow_sql confirm-production <project_id> --run-manifest <path> --pipeline <path>`。
- 如果实际上传的是后续剪辑导出的版本，加 `--final-path <最终发布版.mp4>`；run manifest 继续提供模板和生成来源，履历哈希与当前路径记录最终发布版。
- 测试、预览、模板校准和未采纳成片不得执行确认命令。
- 下次选模板前运行 `python -m bworkflow_sql production-history <project_id> --account <账号>`，优先使用 `recommended_template`。
- 发布后自动归档：`python -m bworkflow_sql complete-publishing <production_run_id> --pipeline <path>`。归档单元是完整交付目录，不是单个 MP4；默认目标为 `G:\2026项目-b站\已发布视频\<已存在月份目录>\<原项目目录名>`。月份目录不存在时使用已发布视频根目录，但仍保留原项目目录名。
- 归档会递归重写 pipeline 中属于原交付目录的绝对路径，包括 `output_dir`、引言、成片、封面和 `artifact_approvals`，随后重新校验审批文件的大小和 SHA-256，再更新 SQLite。目标项目目录已存在时拒绝合并覆盖。
- 已手工整体移动项目目录：改用 `--current-path <新项目目录内的正式成片路径>`；若原交付目录仍存在则拒绝只重绑单个文件。run manifest 作为历史生成证据不修改。

## 冻结配方重渲染与修订

- 先运行只读预检：`python -m bworkflow_sql rerender-production-preflight <production_run_id>`。只有结果为 `reproducible` 才能继续；`external_edit`、`sources_missing`、`version_drift` 和 `legacy_unknown` 都必须停止并说明原因。
- 正式重渲染：`python -m bworkflow_sql rerender-production <production_run_id> --pipeline <path>`。该命令只消费已确认版本的冻结配方，不重新随机选素材，也不允许默认素材回退。
- 重渲染只生成待确认候选，不覆盖原正式成片。已发布 pipeline 保持 `done`，候选写入 `assembly.pending_candidate`。
- 用户验收候选后，继续使用现有 `confirm-production` 创建新修订；不得直接改写旧 `production_runs`。
- 生产命令返回 `repair_required` 时立即停止。代码修复是独立开发任务，不能在生产命令内部自动修改代码再继续渲染。

## 正式成片后的封面阶段

新版 run manifest 在 `confirm-production` 后必须先完成封面，才允许进入发布准备：

1. `cover-context --pipeline <path>` 只读返回完整口播稿和账号风格信息；据此生成恰好 5 个候选文案。
2. 把候选写入 UTF-8 JSON 后运行 `record-cover-copy-options`；必须等用户选择，再运行 `confirm-cover-copy --index <1-5>`。
3. `prepare-cover-generation` 会冻结固定账号人像、独立风格、已确认文案和完整提示词。将其返回的 `portrait_path` 与 `prompt` 原样交给 `imagegen`。
4. 生图模型一次只生成 1 张 4:3 图片，直接生成逐字一致的中文封面文字；只说明品类和多个同类商品，不输入具体 SKU 商品图，也不做程序化贴字。
5. 用 `record-cover-image` 导入候选并展示给用户。通过后运行 `confirm-cover-image`；不通过运行 `reject-cover-image --reason <原因>` 后重新准备并生图。

人像源目录固定为 `G:\2026项目-b站\素材-剪辑\素材-人像`。小燃、小博、小歪、荣荣分别使用四套独立提示词。封面文案证据、提示词包、人像快照、候选图和最终确认均绑定哈希；任一内容变化都会阻断旧确认。

## 发布后蓝链回流

发布归档后，优先使用按任务闭环的一键命令，不要让操作者逐条复制 URL：

```powershell
python -m bworkflow_sql resolve-blue-link-backfill <backfill_id> --workspace-id <Master工作空间UUID> --max-links 5
```

只查看已有失败记录、给用户列链接或准备下一次重跑时，不打开浏览器：

```powershell
python -m bworkflow_sql blue-link-backfill-report <backfill_id> --workspace-id <Master工作空间UUID>
```

日常批处理顺序固定为：Master 商品 ID 唯一匹配自动写库；已知 ID 的库内缺失/重复
保持 Master 数据挂起，标题和浏览器均不得覆盖。`resolve-blue-link-backfill` 只对无 ID
且平台已知的剩余项批量执行严格标题匹配，再对严格失败项执行模糊匹配；有候选的
全部留到最终 JSON 一次性让用户确认，没有候选的按扫描版本领取浏览器租约。不得逐条打断用户。

用户一次性确认或拒绝后，把决定写入 UTF-8 JSON：

```json
{
  "expected_scan_revision": 3,
  "decision_batch_id": "blue-link-job-1-review-1",
  "decisions": [
    {"source_link": "https://b23.tv/example", "action": "confirm", "product_id": "Master商品UUID"},
    {"source_link": "https://b23.tv/reject", "action": "reject"}
  ]
}
```

然后批量提交；确认写库由 Master 完成，拒绝项在下一次调度中继续浏览器兜底：

```powershell
python -m bworkflow_sql confirm-blue-link-title-candidates <backfill_id> --workspace-id <Master工作空间UUID> --decision-file <decisions.json>
python -m bworkflow_sql resolve-blue-link-backfill <backfill_id> --workspace-id <Master工作空间UUID> --max-links 5
```

- 命令先调用 Master 标题候选接口，再原子领取既无商品 ID、也无待确认标题候选的浏览器行；成功和失败回传都携带当前扫描版本与租约令牌。
- `resolve-blue-links --source-link ...` 只用于不连接 Master 的单条诊断，不是日常回流入口。
- 报告读取 Master 已持久化的完整 `unresolved_items`，并按失败类型返回 `unresolved_groups`；面向用户汇报时必须逐组说明数量和原因，并把该组 `sample_links` 中最多三条源链接写成可点击链接。不得只报数量而不给链接。
- 标题决定按单个回流任务整批事务提交。若返回
  `409 stored_slot_conflict:<source_link>`，说明该批一条也没写入；重新读取
  Master 报告，保留已有槽位，排除冲突决定，并用新的稳定
  `decision_batch_id` 提交其余安全项。禁止修改决定内容后复用旧批次 ID。
- 模糊候选最高分并列时不能按列表顺序猜选。用户笼统确认整张清单也不能确定
  具体 UID；该行继续挂起，其他唯一最高分或单候选行仍可整批提交。
- 批处理每条只打开一次，成功和失败都立即回写 Master；不要在同一进程里循环重试。
- 京东默认至少间隔 20 秒；遇到 `pc-frequent-pro.pf.jd.com/?reason=403` 后本地持久熔断两小时，后续京东不再打开，淘宝任务仍可继续。
- `record-blue-link-backfill` 需要 `--workspace-id`，状态、视频身份和四类挂起计数全部以 Master 快照为准。
- 运行前必须启动授权的 CDP HTTP 代理并复用现有登录态 Chrome。执行器只创建和关闭自己的标签页，同时监听当前页跳转和新标签页；不得读取 Cookie、localStorage、密码或浏览器配置。
- 淘宝券页只能点击顶部唯一主商品卡的标题或图片，禁止点击“立即领券”和“更多宝贝推荐”。主商品不唯一、最终没有标准商品 ID 或页面仍是活动专区时保持挂起。
- 京东只接受标准商品页、官方活动页唯一数字 `mainSku`，或官方风控页中唯一的标准商品 `returnurl`；不得从任意域名的 `sku/mainSku` 参数猜商品。
- 浏览器执行器不能指定数据库商品记录。商品库缺失、分类内重复商品 ID、现有蓝链冲突由 Master 保持挂起，不会再次交给浏览器。
- 京东/淘宝红包只有标题和对应活动页域名同时成立时才记为 `ignored_non_product`，不写蓝链且不计入挂起。

常见排障：

| 现象 | 处理 |
|---|---|
| Master 返回的 `browser_pending` 为空 | 当前剩余项可能有标题候选，或已有商品 ID 且属于商品库缺失、重复 ID、旧蓝链冲突；在 Master 处理，不要打开浏览器。浏览器执行必须使用 `browser-leases` 返回的当前版本租约。 |
| 页面出现多个候选商品 | 保持挂起并记录原因，禁止点击推荐位或凭标题猜测。 |
| 只有部分 URL 成功 | 已成功 URL 已逐条写回；失败证据也已写回。只重跑 Master 重新释放为 `browser_pending` 的行，不直接重试 `deferred/suspended`。 |
| Master 任务仍为 `partial` | 只有全部行进入 `matched`、`existing` 或 `ignored_non_product` 才是 `complete`。 |

## workflow-doctor 对外契约

`workflow-doctor` 只输出 `BWorkflowObservation v1`，不再提供旧 raw JSON。
`ready` 和 `blocked` 都表示命令成功完成检查；其中 `blocked` 使用
`ok=true`、`status=blocked` 和逐项 `blocked_by`，不能按进程故障处理。
项目不存在、参数歧义或内部异常会把结构化 failed observation 写到 stdout，
同时返回非零退出码；调用方不要解析 stderr、traceback 或内部 `next/command`
字段。Schema 与三种状态示例位于 `contracts/`。

## 常用操作速查

| 操作 | 入口 | 关键规则 | 验证 |
|---|---|---|---|
| 更换用户音色 | `scripts/swap_voice.py` | 先改 CONFIG 区；IndexTTS 更新 `voice_profiles` 和 `G:\Tools\IndexTTS2.0\outputs\voices\voices.json`；MiniMax 必须克隆到一个新的 `NEW_MINIMAX_VOICE_ID`，旧 voice id 不能覆盖。 | 运行脚本后确认输出 `VERIFY_DB_JSON_OK=1` 和 `SWAP_DONE=1`。 |
| MiniMax 小歪音色 | `account_voice_profiles`、兼容字段 `accounts.minimax_voice_id` | 当前小歪 MiniMax voice id 是 `xiaowai-v6`。App 优先读标准化 provider profile；换音色脚本不得字符串替换业务源码或外部 skill。 | 查询两处 voice id 均为 `xiaowai-v6`。 |
| IndexTTS 小歪音色 | `data\bworkflow.db.voice_profiles`、IndexTTS `voices.json` | 当前小歪参考音频是 `G:\Tools\自己用的音色\小歪10秒新.mp3`。更换时必须同步 DB 和 `voices.json` 指纹。 | `python scripts/_check_xiaowai.py`，路径应指向新参考音频。 |
| 结尾配音 | `accounts.closing_audio_path` | 生成口播 manifest 时 `_closing_manifest_entry(...)` 读取当前用户 `closing_audio_path`；文件存在才写入结尾音频。当前小歪结尾配音是 `G:\2026项目-b站\素材-配音\公共-结尾\小歪\结尾-小歪.mp3`。 | 查询 `SELECT label, closing_audio_path FROM accounts WHERE label='小歪'`；运行 `python -m pytest -q tests/test_workflow_service.py -k closing`。 |
| 弹窗居中 | `bworkflow_sql/ui.py::_center_dialog` | 所有 `CTkToplevel` 应调用 `_center_dialog(dialog)`；该函数优先按父窗口/主窗口居中，父窗口几何不可用时才按屏幕居中。不要在新弹窗里手写 `winfo_screenwidth()` 居中。 | `python -m py_compile bworkflow_sql\ui.py` 和 `python -m pytest -q tests/test_ui_helpers.py`。 |
| 动态商品卡模板检查 | `template-doctor` + `product-card-preflight` | 新槽位先进入中央注册表，模板只声明自己使用的槽位；当前 10 个模板不声明价格段和品类名称。布局先做静态压力预览，再做一条短动画样片。 | 修复报告的模板声明、冻结数据或媒体槽位后重跑；不得通过整卡 PNG 或剪映草稿恢复。 |

Remotion-first 商品卡模板从 CutMe 元数据进入账号模板列表，显示名使用
`{账号名}模板{序号}`。新增模板不维护 SQL/剪映坐标；正式视频只消费
`slotRegistry`、`slotDeclarations`、`coverMediaSlot` 和 `cardPlacement`。
| 字幕语义断行 | `bworkflow_sql/workflow_service.py::split_subtitle_text` | 长分句二次切分时保留数字+单位、英文型号、小数和“的/地/得”结构，优先在“但是/而且/所以”等连词前断。 | `python -m pytest -q tests/test_workflow_service.py -k subtitle`。 |

## MiniMax 换音色流程

| 步骤 | 说明 |
|---|---|
| 1. 准备参考音频 | 支持 `mp3` / `m4a` / `wav`；建议 10 秒到 5 分钟且小于 20MB。中文路径在 Python 脚本内部处理。 |
| 2. 编辑脚本配置 | 修改 `scripts/swap_voice.py` 的 `ACCOUNT_LABEL`、`INDEXTTS_VOICE_ID`、`NEW_AUDIO`、`NEW_MINIMAX_VOICE_ID`。 |
| 3. 运行脚本 | `G:/Tools/IndexTTS2.0/wzf310/python.exe -X utf8 scripts/swap_voice.py`。 |
| 4. 同步配置 | 脚本事务性更新 `account_voice_profiles` 和兼容字段 `accounts.minimax_voice_id`；不得自动改写 `workflow_service.py` 或外部 skill 源码。 |
| 5. 自检 | 必须看到 `SWAP_DONE=1`。如失败，先看 `MINIMAX_REASON`，不要重复占用同一个 MiniMax voice id。 |

## 配音 Provider 切换与排障

| 检查 | 命令或数据 | 判定 |
|---|---|---|
| 待生成数量 | `python -m bworkflow_sql voice-counts <project_id> --account <账号> --voice-provider minimax|indextts` | 必须与随后 `voice` 使用同一个 provider。 |
| 实际生成 | `python -m bworkflow_sql voice <project_id> --account <账号> --voice-provider minimax|indextts` | 省略参数时默认 `minimax`。 |
| 账号配置 | `SELECT provider, voice_id, model, settings_json, enabled FROM account_voice_profiles WHERE account_id=<id>` | 当前 provider 必须有一条启用 profile；旧 account 字段只作兼容后备。 |
| 复用来源 | 查询 `asset_bindings.voice_provider/voice_model/voice_id/synthesis_settings_hash/generation_fingerprint` | 任一生成身份字段变化都应重新生成，不得复用旧音频。 |
| 写入失败 | 检查旧 ready binding 和旧文件仍存在 | 新文件先独立生成；DB 提交后才清理旧文件。 |

新增第三方配音接口时，在 `tts_adapters.py` 实现统一 Provider 契约并在
`WorkflowService._voice_provider_registry(...)` 注册；不要在生成循环、账号页面
或换音色脚本里复制 provider 分支。

## RenderPackage 商品排序策略

`render-package` 和 `render-final-video` 默认使用
`--product-order-strategy price_segment_shuffle`。B-Workflow 仍然把价格过渡段放在
对应价格段商品前面，但只随机该价格段内部的商品；生成后的 RenderPackage 会写入
`output.productOrderStrategy`，使缓存、重渲染和正式成片顺序一致、可复现。

只有用户明确要求旧顺序或稳定复现时，才加 `--product-order-strategy stable`。如果使用
`--mode top --top-uids UID1,UID2`，置顶 UID 必须保持用户给定顺序排在最前面；只有剩余商品
参与段内随机或稳定排序。没有可匹配价格段时保持正常稳定顺序，不打乱整批商品。

## 统一完整 MP4、快速验收与片段缓存

正常流程只产生一个完整 MP4。B-Workflow 把无字幕引言原片、价格/商品推荐段、
账号 `accounts.closing_audio_path` 固定结尾组成同一个 RenderPackage，再只调用一次 CutMe：

```powershell
python -m bworkflow_sql render-final-video <project_id> --account <账号> --intro-video <无字幕引言.mp4> --intro-video-source-plan <source-intro-plan.json> --full-output <完整.mp4> --acceptance-mode quick
```

`render-final-video` 默认 `--subtitle-alignment asr`。所有段落在一个 ASR worker 中批量识别，
按原稿与识别文本的序列匹配定位；文案覆盖率低于 60% 直接阻断，不回退到按字数均分。
`--intro-video-source-plan` 只提供引言原稿，不复用旧 timing；也可用 `--intro-video-text-file`
显式提供原稿。无引言原稿时必须阻断。

CutMe 使用同一 `output.subtitles.styleId` 烧录引言、商品和结尾字幕；合并前每段都按
`audio.loudnessTarget` 归一化。整片先测量，只有满足响度、真峰值和 LRA 容差才跳过
AAC 母带重编码；`--acceptance-mode full` 仍会独立扫描最终 AAC 文件。

结尾从 `G:\2026项目-b站\素材-自动剪辑\1-通用\*整片结尾*.mp4` 的排序候选中
确定性选择，并把选择结果和 seed 写入 RenderPackage；账号固定
`closing_audio_path` 作为口播混入该视频。候选缺失或结尾视频短于口播会在构包阶段阻断，
不得回退旧的六套静态 CutMe outro 模板，也不得把 `引导三连` 等中段素材当作整片结尾。

标准交付目录优先使用 `--delivery-dir <dir>`，不要让 Agent 临时拼多个输出路径：

```powershell
python -m bworkflow_sql render-final-video <project_id> --account <账号> --intro-video <intro.mp4> --delivery-dir <交付目录> --acceptance-mode quick
```

传入 `--delivery-dir` 后，只把 `完整成片-<timestamp>.mp4` 写到交付目录一级。
不要再创建 `01_最终成片` 二级目录，也不要把目录名、账号名塞进文件名。验收截图写入
`02_验收证据\<timestamp>\frames\`，RenderPackage、片头字幕 ASS 等过程文件写入
`03_过程记录\<timestamp>\`。项目级片段缓存仍在
`data\workspace\project-<id>\render\final-video-cache\`，不进入交付目录。该共享目录由
CutMe 跨进程互斥；片段以内容键命名并原子发布，失败批次只更新
`clip-cache-manifest.in-progress.json`，完整成片和母带成功后才替换正式 manifest。

`--acceptance-mode` 分四档：`none` 只保留文件/ffprobe 级验证；`quick`
用于常规生产快验，不跑 loudnorm、不抽验收帧；`visual` 会抽关键帧但不跑
完整 loudnorm；`full` 同时跑关键帧和完整 loudnorm，适合最终归档验收。命令
返回和 run manifest 会记录 `timings`，用于复盘 package、CutMe 渲染、
ffprobe、抽帧、loudnorm 各阶段耗时。

CutMe fast-final 会在上面的项目级缓存目录写 `clip-cache-manifest.json`，用于跨运行复用
未变化的引言、价格、商品和结尾段。manifest 同时记录命中/重渲染数量、阶段耗时、
视频编码和母带证据，并由 B-Workflow 写入 run manifest。这个缓存是加速项，不是前置
条件：manifest 不可读或某个 clip 缺失时，系统必须安全重渲染对应片段，不能因此阻断生成。

当前生产默认保持 `libx264 veryfast -crf 23`，不是固定 6 Mbps。项目 17 的代表性完整
实测中，价格转场批处理后的首次生成约 `407.84s`，24/24 命中重渲染约 `172.09s`；
另一次 `superfast` 完整实验为 `471.45s` / `160.90s`，没有证明它能稳定改善首次总耗时，
因此不得只根据 60 秒编码切片或 FFmpeg 的硬件编码器列表修改生产默认值。

为了让缓存稳定生效，同一套任务输入下的“随机视觉项”必须可复现：
`output.subtitles.styleId` 由 B-Workflow 按项目/账号/模板/媒体模式/排序策略稳定选择；
CutMe 默认推荐背景图按 package seed 稳定选择，不随每次运行或价格段内商品随机顺序漂移。

最终汇报必须包含 `price_transition_report`。尤其是 `--mode top` 时，置顶商品
会先出现，价格过渡段排在置顶商品之后；不要只说“有/没有价格过渡”，要说明第一段
价格过渡在几个置顶商品之后出现。

### Final MP4 字幕样式

`render-final-video` / `render-package --output-mode final_mp4` 默认写入
`output.subtitles.enabled=true` 和 `styleScope="global"`，并从 CutMe 的全局
生产样式池里按任务输入稳定选择一个 concrete `styleId`。当前生产池为：
`classic_white`、`impact_yellow`、`panel_white`、`warm_cream`、`tech_cyan`、
`orange_energy`。

B-Workflow 只负责选择并写入 styleId，以及生成 segment-local
`subtitles[]`；不要在 SQL 项目里复制 CutMe 的 ASS 视觉参数。旧 styleId
兼容由 CutMe 处理。需要先看效果时，在 CutMe 仓库运行：

```powershell
python -m cutme --preview-subtitle-styles --output G:\workspace\PC-Bilibili-workflow-sql\data\workspace\manual-tests\subtitle-style-preview\subtitle-style-preview.png
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
| Master 暂不可用 | `master_unavailable` | 在同步中心确认启动本地 Master 服务并重试预览 |
| Master 契约或版本错误 | `invalid_master_contract` / `unsupported_contract_version` | 不允许默认值或旧接口兜底；先修 owner 数据/版本再同步 |
| 预览后方案变化 | `stale_master_preview` | 重新预览，核对新的 snapshot id 后再应用 |
| 品类过渡文案不入库 | MD parser 只识别 `## 价格过渡文案` | 过渡文本硬编码在脚本里 |
| shell 环境中文乱码 | Git Bash 的 stdin 编码 | 业务逻辑写 `.py` 文件，不走 shell 管道 |

## 验证命令

| 场景 | 命令 |
|---|---|
| 最小 UI 回归 | `python -m pytest -q tests/test_ui_helpers.py` |
| 结尾配音回归 | `python -m pytest -q tests/test_workflow_service.py -k closing` |
| 字幕断行回归 | `python -m pytest -q tests/test_workflow_service.py -k subtitle` |
| 模板校准回归 | `python -m pytest -q tests/test_render_package_jianying.py tests/test_cli_render_package.py tests/test_jianying_engine_display_video.py` |
| 引言场景强制对齐回归 | `python -m pytest -q tests/test_forced_alignment.py tests/test_cutme_intro.py tests/test_intro_timeline.py` |
| 常用服务回归 | `python -m pytest -q tests/test_workflow_service.py tests/test_ui_helpers.py tests/test_repositories.py tests/test_sync_service.py` |
| Master 契约边界 | `python -m pytest -q tests/test_master_contracts.py tests/test_master_snapshot_sync.py tests/test_master_snapshot_repository.py tests/test_master_snapshot_cutover.py tests/test_master_catalog_cutover.py tests/test_master_raw_client_forbidden.py` |

## CutMe 引言场景时间轴

`bworkflow_sql.intro_timeline.align_intro_plan_scenes_with_asr(...)` 保留原函数名作为
调用兼容层，实际把 CutMe 的 `intro_plan.scenes[].text` 和整段引言配音送入精确原文
强制对齐器，输出 `scenes[].timing`。

关键规则：

- 对齐前必须校验 `scenes[].text` 拼接后与 `full_script` 一致，不能让 LLM 改字后继续对齐。
- 引言和正文统一走 `bworkflow_sql.forced_alignment`；自由 ASR 识别结果不得参与字幕定时，也不得在 CutMe 内另建对齐分支。
- 首次使用先运行 `scripts\setup_subtitle_forced_alignment.ps1`。正式对齐结果按音频、精确原文、模型和切句规则缓存。
- CutMe 只消费 `scenes[].timing`，并根据 `hook_open`、`pain_points`、`self_check`、`priority_preview` 控制产品展示和引导三连素材。

## CutMe 引言页面新链路

`工具 -> CutMe 引言` 现在支持两条链路：

- 选择 `引言计划 JSON`：走新链路。页面会先校验 `intro_plan.full_script` 与当前引言文案一致，再按 `G:\2026项目-b站\素材-自动剪辑\{一级品类-二级品类}` 随机选择三段不重复产品展示素材，并从 `1-通用` 文件夹随机选择文件名包含 `引导三连` 的视频。缺产品展示或缺引导三连时，在渲染前直接报错。
- 不选择 `引言计划 JSON`：走旧链路，继续使用 `素材文件夹 + cutme_service.generate_intro_video(...)`，不会得到 `selected_assets` 和 ASR 场景 timing。

新链路准备好的中间文件会写入：

```text
data\workspace\project-{project_id}\intro\intro-plan-{script_block_id}-{account}.json
data\workspace\project-{project_id}\intro\cutme-config-{script_block_id}-{account}.json
```

`cutme-config` 通过 `intro_plan_path` 交给 `python -m cutme` 渲染。页面日志会显示准备后的 `intro_plan`、CutMe 配置、素材预检结果、是否执行精确原文强制对齐，以及最终选中的素材路径。

`prepared intro_plan` 会写入 `pc_workflow.seed`，`cutme-config` 会写入 `"seed"`。生产 seed 每次准备 CutMe 渲染时重新生成，不按账号、品类或引言固定绑定；同一个账号/品类/引言块重复生成，也应该得到不同视觉变体。当前先走文件契约，不急着入库。

当前新链路还会处理四件事：
- 根据 `visual_event_specs[].trigger_text` 复用同一份强制对齐锚点，写入 `visual_events[].timing`，让文字卡片按配音逐项入场。
- 从 `G:\2026项目-b站\素材-自动剪辑\1-音效` 精确匹配 6 个 `sfx_*.wav` 文件；缺音效只 warning，不阻断渲染。
- 引言配音会先按口播增强档 loudnorm，CutMe 成片导出后还会对最终 MP4 再做一次同目标母带：`I=-11 LUFS / TP=-1.0 dB / LRA=11`，最终音频为 AAC 48kHz。
- CutMe 渲染时会把选中的产品展示/引导三连视频先转成 workspace 内的稳定 MP4：H.264、1920x1080 cover crop、30fps、GOP 30、yuv420p、`+faststart`，并丢弃素材原声；原始素材不修改。这个步骤用于避免 HyperFrames 因素材关键帧稀疏出现 seek 卡帧。
- CutMe 根据 seed 生成 `visual_variant`，控制颜色、布局偏移、卡片样式、背景图选择、产品镜头轻微偏移和入场节奏。没有 seed 时保持旧固定样式。
- `general` 模板还会按 seed 为 7 个段落分别选择 `a/b/c` 结构方案，具体方案图在 CutMe 仓库 `design-previews/general-random-v2/`。

临时测试视频、抽帧、探针 config、对比样片不要再直接堆在正式 `intro\` 根目录。需要测试时放到：

```text
data\workspace\manual-tests\{test-name}\
```

同一测试主题的配置、短样片、RenderPackage、ASS、抽帧、缓存和 README
都收拢在该目录；默认不写正式 `.pipeline.json`。CutMe 如生成临时 job，验证后
只清理该次明确生成的 job，不把 `render_jobs` 当长期验收目录。

## 商品正文措辞门禁

商品正文写入正式 Markdown 后、用户审稿前运行：

```powershell
python -m bworkflow_sql copy-lint <project_id>
```

该命令只检查 `## 商品文案` 下的正文版本，不检查资料采集包、价格过渡或引言。它会硬拦截两类高置信问题：

- 把“主推、重点款、低佣、选品池、一百到两百档”等内部选品身份念给观众；
- 把“页面标注、商品页、详情页、官网写的、资料显示、据测评”等采集过程念进正式口播。

“预算在一百到两百元”“不必为更高参数加预算”“UPF 做到一百加”等观众可用的预算、选择边界和参数表达允许通过。命中结果会返回 UID、正文版本、文件行号、原句和修改方向。

`script-doctor` 复用同一套 lint。任何商品正文版本命中时，该商品不计入 `product_copy_ready`，`next.action=fix_product_copy_language`，并禁止 Markdown 同步、配音和组装。`SyncService.sync_markdown(...)` 在写数据库前也会强制执行该检查，直接运行同步命令不能绕过。修正后必须依次重新运行 `copy-lint` 和 `script-doctor`。

硬门禁通过后，再运行整篇风格软审计：

```powershell
python -m bworkflow_sql copy-audit <project_id> --voice-profile zhaoer
```

`copy-audit` 检查赵二口吻中已经排除的抽象收尾，并从全文视角报告重复的“商品主体 + 抽象判断”结构。它返回具体 UID、正文版本、文件行号和原句；`script-doctor` 也会把同一结果列为 `product_copy_style_warning`。这些警告不降低 `product_copy_ready`、不拦截 Markdown 同步，也不会自动改写正文。审稿阶段应逐条做删除测试：前文已经能帮助选择时直接删掉尾句，需要保留判断时改成具体条件、取舍或使用后果。

## CutMe 引言写作链路

第一阶段“写引言文案”不再直接让 AI 自由写完整开头，而是先写模板槽位 JSON，再由仓库把槽位渲染成固定结构的引言文案和 CutMe `intro_plan`。

默认模板是 `pain_avoidance_priority_v1`。槽位 JSON 至少包含这些字段：

```json
{
  "category": "键盘",
  "common_mistake_1": "轴体名字",
  "common_mistake_2": "灯效",
  "common_mistake_3": "热插拔",
  "pain_1": "手感不稳定",
  "pain_2": "声音太吵",
  "pain_3": "长时间打字累",
  "scene_1": "办公和码字",
  "criteria_1": "稳定手感",
  "flashy_selling_point": "炫酷 RGB",
  "scene_2": "打游戏",
  "criteria_2": "触发速度",
  "criteria_3": "键位响应",
  "scene_3": "宿舍或者夜里用",
  "criteria_4": "声音控制",
  "bad_result": "影响别人休息",
  "standard_1": "手感",
  "standard_2": "连接稳定性",
  "standard_3": "做工"
}
```

生成命令：

```powershell
python -m bworkflow_sql intro-plan <project_id> --slots <slots.json> --label 引言1
```

输出位置：

```text
data\workspace\project-{project_id}\intro\intro-slots-引言1.json
data\workspace\project-{project_id}\intro\source-intro-plan-引言1.json
```

命令会把完整引言写进项目 Markdown 的 `## 引言文案 / ### 引言1`，并把 `source-intro-plan-*.json` 作为 CutMe 的源计划文件保留下来。之后进入 `工具 -> CutMe 引言` 时，如果当前引言文案与 `source-intro-plan-*.json` 的 `full_script` 一致，页面会自动匹配该计划文件，不需要手动选择。

文案审稿期间不要添加 `--sync`。修改引言时必须修改槽位并重新执行
`intro-plan`，不能只改 Markdown；用户明确说“定稿”后再单独执行 Markdown
同步和配音。

### 价格过渡结构化自动剪辑计划

新写的价格过渡不要依赖通用关键词推断。先准备包含所有价位段的 JSON：

```json
{
  "transitions": [
    {
      "price_range_label": "100-200元",
      "block_label": "正文",
      "transition_text": "一百到两百元重点看水流稳定和档位调节，适合正畸人群。",
      "audience": "适合正畸人群",
      "items": [
        {"label": "水流稳定", "trigger_text": "水流稳定"},
        {"label": "档位调节", "trigger_text": "档位调节"}
      ]
    }
  ]
}
```

写入命令：

```powershell
python -m bworkflow_sql price-transition-plan <project_id> --plan <price-transition-plan.json>
```

该命令不带 `--sync` 时只更新正式 Markdown，并保存机器计划：

```text
data\workspace\project-{project_id}\price-transitions\source-price-transition-plan-set.json
```

计划存在后进入严格模式。正文哈希、价位段、版本标签或触发词不匹配时，
RenderPackage 会报告 `price_transition_plan` 缺失并停止。最终 MP4 使用默认
ASR 字幕对齐结果重算画面项出现时间，CutMe 再校验
`priceTransitionPlanVersion=1.0.0` 和 2-3 个结构化画面项。

同一价格段可以在 `transitions[]` 中保存多个不同 `block_label` 的版本；每个
非空 Markdown 版本都必须有对应计划。`assemble-plan` 和 `assemble` 统一传入
当前 `--episode-id`，商品正文和价格过渡各随机选择一个版本并在本期固定。

新增模板时，先在 CutMe 仓库的 `intro_templates` 中新增模板和 `visual_cues` 契约，再用本仓库 `intro-plan --template <template_id>` 生成计划文件。不要只改提示词而不更新模板契约，否则 CutMe 无法稳定知道哪些段落要插产品展示和引导三连。

回归验证：

```powershell
python -m pytest -q tests/test_intro_plan_writer.py tests/test_cutme_intro.py tests/test_intro_timeline.py
```

## RenderPackage 跨仓边界

- 正常生产只运行 `python -m bworkflow_sql render-final-video ...`。
  B-Workflow 内部统一经过 `CutMeAdapter`；不要从业务
  模块直接调用 CutMe npm、拼 `cutme.render_cli` argv，或解析人类可读路径。
- B-Workflow 的代表性 producer fixture 位于
  `contracts/examples/cutme-render-package.v1.json`。修改 RenderPackage 字段后，
  同时运行本仓测试、CutMe 契约测试和 TotalControl
  `scripts\boundary2-check.ps1`。
- `python -m cutme.render_cli ...` 是低层调试/契约入口，不是公开 next 指令。
  stdout 只有一个版本化 JSON；日志在 stderr。

## Final MP4 run manifest

Every schema-v2 `render-final-video` run writes a run manifest under
`data\workspace\project-{project_id}\runs\episodes\{episode_key}\final-video-*.run-manifest.json` and
returns it as `run_manifest_path`. This file is the evidence for one concrete
generation run: selected account/template/order/media mode, RenderPackage path,
intro video path, product/full MP4 paths, acceptance frames,
`price_transition_report`, segment fingerprints, and file fingerprints.

The run manifest is not a reusable copy asset and is not the workflow phase
state. Reusable copy/parameter assets live in the WriteSpace asset library;
the episode `.pipeline.json` records that task's production selection and phase; the run
manifest records what one output actually used. If a user-selected output
directory is moved or deleted later, treat the missing MP4 as a missing
historical artifact and rerun generation. Do not treat it as a broken asset
library or stale `.pipeline.json`. For normal workflow production, pass
`--pipeline <path-to-.pipeline.json>` to `render-final-video` so the latest
manifest and MP4 paths are written back for TotalControl `workflow.ps1
status/next --episode-id <episode:...>`.

## Resource lifecycle audit and reconciliation

Resource cleanup is event-driven rather than a periodic disk sweep. Successful
product-image jobs register a delayed cleanup candidate immediately; moving an
image binding to a new template or output path registers the replaced generated
PNG. Periodic work is only a reconciliation backstop.

```powershell
python -m bworkflow_sql resource-audit <project_id> --pipeline <.pipeline.json>
python -m bworkflow_sql resource-reconcile <project_id> --pipeline <.pipeline.json>
python -m bworkflow_sql resource-cleanup-list <project_id> --pipeline <.pipeline.json>
python -m bworkflow_sql resource-cleanup-plan <project_id> --pipeline <.pipeline.json> --account <账号> --kind <resource_kind>
python -m bworkflow_sql resource-cleanup-delete --batch-id <batch_id> --confirm <token>
python -m bworkflow_sql resource-history <project_id> --account <账号> --kind <resource_kind> --state <state>
```

The first command is read-only. The second only writes high-confidence candidates
to `resource_cleanup_candidates` and corrects broken `ready` bindings to `missing`;
neither command deletes files. `resource-cleanup-list` applies every permanent-delete
gate without writing. `resource-cleanup-plan` stores an exact fingerprinted batch
and returns its one-time token; it still does not delete. Only the final command,
after explicit user confirmation, permanently deletes the unchanged batch.

There is no automatic deletion and no quarantine directory. Missing pipeline state
makes ordinary inactive assets `uncertain`. Manual/source assets, formal productions,
run manifests, immutable spoken scripts, and superseded formal outputs are outside
ordinary cleanup. Product-image jobs retain seven days; replaced generated images
and voices retain fourteen days. A changed path, size, mtime, fingerprint, binding,
pipeline or production reference invalidates the prepared batch. Successful deletion
keeps its candidate, batch-item and state-event tombstones.
`resource-history` queries those append-only creation, update, invalidation and
deletion events without scanning the filesystem.
