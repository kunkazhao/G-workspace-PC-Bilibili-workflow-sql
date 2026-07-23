# Jianying Engine (Retained Historical Runtime)

This directory keeps the former B-Workflow SQL draft generator for historical
projects. Jianying is no longer exposed through the public UI/CLI or phase-7
workflow, and this runtime receives no new product-card template or slot
compatibility work.

Runtime entry:

- `generate_jianying_draft.py`

Support files:

- `python_env.py`: resolves the engine-local Python environment.
- `image_index.py`: optional image-index lookup for manifests that do not already contain media paths.
- `requirements-jianying.txt`: dependencies for the engine-local `.venv`.

Default mutable data lives outside this code directory:

- `data/jianying_engine/image_index.json`

The application resolves the engine in this order:

1. `BWORKFLOW_JIANYING_ENGINE_DIR`
2. `scripts/jianying_engine`
3. archived legacy `C:\Users\zhaoer\.codex\skills_archived\b-workflow-20260625\scripts`

Keep this directory focused on draft generation only. Voice generation, spoken-script assembly, project sync, and database logic belong in `bworkflow_sql/`.
