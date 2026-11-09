"""Repository paths and pinned upstream versions."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# tau2-bench is pinned to the label-corrected 1.x release that has the 114-task
# retail `base` split used by the AWS blog. See docs/AUDIT.md.
TAU2_TAG = "v1.0.1"
TAU2_COMMIT = "fc0055dc4e0a316c3f83133267fbd6faaa770992"
TAU2_DIR = REPO_ROOT / "third_party" / "tau2-bench"
TAU2_DATA_DIR = TAU2_DIR / "data"

DATA_DIR = REPO_ROOT / "data"
HOLDOUT_DIR = DATA_DIR / "holdout"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
RUNS_DIR = DATA_DIR / "runs"
LLM_CACHE_DIR = DATA_DIR / "cache" / "llm"

# Official evaluation task files. Only scripts/build_holdout.py may read these;
# tests/test_isolation_static.py enforces that nothing under src/ references them.
OFFICIAL_TASK_FILENAMES = ("tasks.json", "split_tasks.json", "tasks_voice.json")
