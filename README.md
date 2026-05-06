# B-Workflow SQL

V2 desktop workflow for Bilibili product-content production.

The project treats SQLite as the local source of truth:

- Master scheme defines the product boundary.
- Markdown remains the writing format, but sync only imports products already in the local project.
- Asset folders store real files. The database stores paths, status, file metadata, and mappings.
- Legacy scripts from `G:\workspace\PC-Bilibili-workflow` and Codex skills are reused through adapters.

## Run

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
- 资产中心
- 同步中心
- 用户管理
- 设置

Workflow:

- 生成配音
- 组合口播稿
- 生成剪映草稿

The UI is intentionally direct and database-first. JSON/Markdown support is compatibility and import/export, not the primary app state.

## Recommended Flow

1. Open `品类项目`, create a category project, and fill in Master workspace/scheme, MD path, image/video/voice roots, and output root.
2. Use `预览 Master 方案变化`, then sync Master products into SQLite after confirming the product boundary.
3. Sync the MD copy. The importer only accepts products that are already in the current Master scheme; extra MD products are reported but not imported.
4. Sync asset folders. Image/video/voice files are matched to current products by UID in the filename or path and saved as database bindings.
5. Use the workflow pages in order: `生成配音` -> `组合口播稿` -> `生成剪映草稿`.

## Markdown Contract

The parser uses fixed headings instead of guessing:

- `## 引言文案`
- `## 商品文案`
- `## 价格过渡文案`
- `## 商品顺序`

Product headings should include UID, for example `### 竹林鸟夜莺Z1-YXEJ002-59元`. Multiple versions can be written with `#### 正文`, `#### 版本2`, or similar labels under the same intro/product/price block.
