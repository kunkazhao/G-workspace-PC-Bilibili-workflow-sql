# B-Workflow SQL

V2 desktop workflow for Bilibili product-content production.

The project treats SQLite as the local source of truth:

- Master scheme defines the product boundary.
- Markdown remains the writing format, but sync only imports products already in the local project.
- Asset folders store real files. The database stores paths, status, file metadata, and mappings.
- Runtime workflow is database-first: voice generation writes SQLite asset bindings directly, spoken-script assembly reads SQLite directly, and `audio_segment_registry.json` is not used by the new workflow.
- Old project files are only read by explicit migration/import helpers.

## Run

Install the UI theme dependency first:

```powershell
python -m pip install -r requirements.txt
```

```powershell
.\start_app.bat
```

Or:

```powershell
python -m bworkflow_sql.main
```

## First Version Scope

Configuration:

- 品类项目
- 文案中心
- 资产中心：按品类、用户、文案类型查看商品文案、引言文案、价格过渡文案，以及图片、视频、配音映射状态
- 同步中心
- 用户管理
- 设置

Workflow:

- 生成配音
- 组合口播稿
- 生成剪映草稿

Tools:

- 单独配音：directly synthesize pasted text or a whole MD document without binding the result to a category project.
- 导出字幕 SRT：select a spoken-script MD, reuse its generated manifest and audio durations, and export `字幕-<口播稿 MD 文件名>.srt`.

The UI is intentionally direct and database-first. JSON/Markdown support is compatibility and import/export, not the primary app state.

## Recommended Flow

1. Open `品类项目`, create a category project, choose the Master category/scheme, then select the source MD and image/video/voice roots.
2. Use `预览 Master 方案变化`, then sync Master products into SQLite after confirming the product boundary.
3. Sync the MD copy. The importer only accepts products that are already in the current Master scheme; extra MD products are reported but not imported.
4. Sync asset folders. Image/video/voice files are matched to current products by UID in the filename or path and saved as database bindings.
5. Use the workflow pages in order: `生成配音` -> `组合口播稿` -> `生成剪映草稿`. The spoken-script output MD is chosen in `组合口播稿`, not in the category project.

Legacy migration helpers are available in `资产中心` and `用户管理`:

- `导入旧项目用户/音色` imports old account and voice-profile data into SQLite.
- `导入屏幕挂灯资产` imports the existing `数码-屏幕挂灯` project, MD copy, images, videos, and voice mappings from the old workflow folders.

Output rules:

- `商品文案 MD` is the source copy document.
- `口播稿输出 MD` is selected in the `组合口播稿` workflow. It is the final combined spoken script, and the assembly step overwrites the whole file.
- The spoken script manifest is an internal task file under `data/workspace/project-<id>/manifests/`.
- Internal generated files are kept under `data/workspace`.
- Jianying drafts are written to `E:\剪辑-剪映\草稿\JianyingPro Drafts`.
- Standalone voice files default to `G:\2026项目-b站` and do not write `asset_bindings`.

## Voice Generation Notes

- Project voice generation writes SQLite asset bindings for matched script blocks.
- Standalone voice generation accepts either a configured user voice or one uploaded reference audio file. The two voice sources are mutually exclusive.
- Standalone MD input supports `.md` files only and sends the whole cleaned document as one dubbing job.
- Generated WAV files go through a conservative silence cleanup pass:
  - leading silence over `300ms` is trimmed to `120ms`;
  - leading silence shorter than `100ms` is padded to `100ms`;
  - trailing silence over `500ms` is trimmed to `200ms`;
  - internal silence up to `300ms` is left untouched;
  - internal silence from `300ms` to `800ms` is trimmed to `220ms`;
  - internal silence over `800ms` is trimmed to `350ms`.
- Fine-grained word-internal pause correction is intentionally left for a later ASR-alignment tool instead of aggressive automatic trimming during generation.

## Jianying Draft Notes

- Draft generation is delegated to `C:\Users\zhaoer\.codex\skills\b-workflow\scripts\generate_jianying_draft.py`.
- Product images remain on the main media track. Product videos are also written as `display_video_path` and placed on the separate `display_video` track.
- Voice clips use a `100ms` timeline gap between adjacent clips.
- Template slots normally use 1920x1080 canvas rectangle coordinates: `x`, `y`, `width`, and `height`.
- `小燃-模板1` uses Jianying position-panel coordinates instead: `x=-830`, `y=-77`, `width=970`, `height=590`, with `coordinate_mode="clip_transform_pixels"`.
- For fast alignment checks, regenerate `data/tmp_jianying_probe/xiaoran1-three-products.manifest.json` and run `data/tmp_jianying_probe/run_xiaoran1_three_product_draft.py`. The smoke draft contains 3 products and skips subtitles.

## Markdown Contract

The parser uses fixed headings instead of guessing:

- `## 引言文案`
- `## 商品文案`
- `## 价格过渡文案`
- `## 商品顺序`

Product headings should include UID, for example `### 竹林鸟夜莺Z1-YXEJ002-59元`. Multiple versions can be written with `#### 正文`, `#### 版本2`, or similar labels under the same intro/product/price block.

Existing old MD files are also supported when a block has no `####` title and uses repeated `**手动录入**` markers. Those blocks are imported as `正文`, `正文2`, or `引言`, `引言2`, while preserving the nearby `script_id` comments for matching and auditing.

The V2 outline creator writes product headings as `价格-UID-商品名`, for example:

```md
### 59元-YXEJ002-竹林鸟夜莺Z1
```

The parser still accepts the older `商品名-UID-价格` heading format for existing documents.
