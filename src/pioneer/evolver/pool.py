"""[INDEPENDENT] The pool of verified tasks that parents are drawn from.

Rows are ``{task, meta, ...}`` dicts as the generation ledgers write them. Only
admitted rows (``passed`` absent or true) enter; instances are unique by their
content signature.
"""
import json
from pathlib import Path

from pioneer.schemas import Intent, Scenario


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def load_pool(paths) -> list[dict]:
    """Admitted rows from the given jsonl files, first occurrence of an instance wins."""
    seen, pool = set(), []
    for path in paths:
        for row in read_rows(path):
            if not row.get("passed", True) or "task" not in row or "meta" not in row:
                continue
            signature = row["meta"]["instance_signature"]
            if signature not in seen:
                seen.add(signature)
                pool.append(row)
    return pool


def row_id(row: dict) -> str:
    return row["task"]["id"]


def parent_scenario(row: dict) -> Scenario:
    """The scenario a pooled task came from (depth-0 rows from the Initializer carry none)."""
    if row.get("scenario"):
        return Scenario.model_validate(row["scenario"])
    meta, task_id = row["meta"], row["task"]["id"]
    if meta["depth"] != 0 or not task_id.startswith("depth0:"):
        raise ValueError(f"{task_id}: a deeper task must carry its scenario")
    atomic = row.get("atomic_action") or task_id[len("depth0:"):].rsplit(":", 1)[0]  # "depth0:<atom>:<index>"
    return Scenario(scenario_id=meta["scenario_id"], depth=0, stressors=meta["stressors"],
                    intents=[Intent(atomic_action=atomic)], expected_outcome=meta["expected_outcome"])
