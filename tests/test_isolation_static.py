"""Evaluation isolation: generation code must never reference official task files.

Only scripts/build_holdout.py (PLAN Step 1) may read them.
"""

from pioneer.paths import OFFICIAL_TASK_FILENAMES, REPO_ROOT

ALLOWED = {REPO_ROOT / "scripts" / "build_holdout.py"}


def test_no_official_task_file_references():
    offenders = []
    for root in (REPO_ROOT / "src", REPO_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            if path in ALLOWED or path.name == "paths.py":
                continue
            text = path.read_text(encoding="utf-8")
            for name in OFFICIAL_TASK_FILENAMES:
                if name in text or "get_tasks(" in text or "get_tasks_split" in text:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")
                    break
    assert not offenders, offenders
