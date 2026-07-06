# Template Calibration Runner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a standard template-calibration runner so future Jianying calibration uses a fixed checklist instead of agent guesses.

**Architecture:** Store representative calibration targets in a UTF-8 JSON file under `config/`. Add a B-Workflow CLI command that reads either one named target or all active targets, runs the existing doctor/product-images/template-calibrate sequence, validates the generated probe manifest, and outputs JSON report rows. Reuse `WorkflowService` methods rather than shelling out.

**Tech Stack:** Python stdlib JSON/pathlib, existing `WorkflowService`, pytest.

### Task 1: Add Calibration Checklist Contract

**Files:**
- Create: `config/template-calibration-targets.json`
- Create: `bworkflow_sql/template_calibration_runner.py`
- Test: `tests/test_template_calibration_runner.py`

**Steps:**
1. Write failing tests for loading active targets, filtering by `target_id`, and rejecting missing required fields.
2. Implement a loader that returns normalized dicts with `id`, `project_id`, `account`, `template_id`, `product_uid`, and `draft_name`.
3. Add seed targets for current accepted templates: `xiaobo-template2`, `xiaoran-template2`, and deferred `rongrong-template1`.

### Task 2: Add Standard Runner

**Files:**
- Modify: `bworkflow_sql/template_calibration_runner.py`
- Test: `tests/test_template_calibration_runner.py`

**Steps:**
1. Write failing test using a fake workflow: doctor fails with image issues, runner calls product image regeneration, reruns doctor, then calls `template_calibration_probe`.
2. Write failing test for manifest validation: top-level display template, image path template folder, and `display_video_slot.templateId` must match selected template metadata.
3. Implement `run_template_calibration_targets(...)` with `dry_run`, `regenerate_images`, and `draft_name_suffix`.

### Task 3: Add CLI Command

**Files:**
- Modify: `bworkflow_sql/cli.py`
- Test: `tests/test_cli_render_package.py`

**Steps:**
1. Add parser test for `template-calibrate-runner --target xiaobo-template2 --draft-suffix v3`.
2. Add command test confirming CLI passes arguments to workflow/runner and prints JSON.
3. Register command in `DISPATCH`.

### Task 4: Document The Standard Workflow

**Files:**
- Modify: `docs/operator-runbook.md`
- Modify: `G:\workspace\Bilibili-TotalControl\docs\COMMANDS.md`
- Modify: `G:\workspace\Bilibili-TotalControl\docs\GOTCHAS.md`

**Steps:**
1. Document the checklist file as the source of truth for calibration targets.
2. Document the runner command and explain that agents should not choose UID/template ad hoc when a checklist target exists.

### Task 5: Verify

**Commands:**
```powershell
cd G:\workspace\PC-Bilibili-workflow-sql
python -m pytest -q tests/test_template_calibration_runner.py tests/test_cli_render_package.py tests/test_template_doctor.py tests/test_product_image_generation.py tests/test_render_package_builder.py tests/test_render_package_jianying.py
python -m py_compile bworkflow_sql/cli.py bworkflow_sql/workflow_service.py bworkflow_sql/template_calibration_runner.py

cd G:\workspace\Bilibili-TotalControl
scripts\docs-check.ps1 -RepoPath G:\workspace\PC-Bilibili-workflow-sql
```
