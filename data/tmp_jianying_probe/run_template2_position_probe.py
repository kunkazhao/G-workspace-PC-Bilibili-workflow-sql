"""小博-模板2 与 小歪-模板2位置验证：生成两个剪映草稿。"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(r"G:\workspace\PC-Bilibili-workflow-sql")
PROBE_DIR = REPO_ROOT / "data" / "tmp_jianying_probe"
ENGINE_DIR = REPO_ROOT / "scripts" / "jianying_engine"
GENERATOR = ENGINE_DIR / "generate_jianying_draft.py"
ENGINE_PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
LEGACY_PYTHON = Path.home() / ".codex" / "skills" / "b-workflow" / ".venv" / "Scripts" / "python.exe"
PYTHON = ENGINE_PYTHON if ENGINE_PYTHON.exists() else LEGACY_PYTHON
DRAFT_ROOT = Path(r"E:\剪辑-剪映\草稿\JianyingPro Drafts")
BACKGROUND_IMAGE = Path(r"G:\2026项目-b站\素材-剪辑\1-背景图\背景1 (1).png")

JOBS = [
 {
 "label": "小博-模板2 (x=1015 y=154 w=680 h=520,面板像素)",
 "manifest": PROBE_DIR / "xiaobo2-position-test.manifest.json",
 "draft_name": "xiaobo2-位置测试-X1015-Y154",
 },
 {
 "label": "小歪-模板2 (x=-843 y=-34 w=1037 h=528, clip_transform_pixels)",
 "manifest": PROBE_DIR / "xiaowai2-position-test.manifest.json",
 "draft_name": "xiaowai2-位置测试-X843-Y34",
 },
]

COMMON = [
 "--draft-root",
 str(DRAFT_ROOT),
 "--background-image",
 str(BACKGROUND_IMAGE),
 "--skip-subtitles",
 "--allow-replace",
]

def run_job(job):
 cmd = [
 str(PYTHON),
 str(GENERATOR),
 "--manifest",
 str(job["manifest"]),
 "--draft-name",
 job["draft_name"],
 *COMMON,
 ]
 print(f"\n===== {job['label']} =====")
 print("CMD:", " ".join(cmd))
 completed = subprocess.run(cmd, cwd=str(REPO_ROOT))
 print(f"-> exit={completed.returncode}")
 return completed.returncode

def main():
 rc_total = max((run_job(job) for job in JOBS), default=0)
 sys.exit(rc_total)

if __name__ == "__main__":
 main()
