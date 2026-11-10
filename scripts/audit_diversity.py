"""[INDEPENDENT] Check admitted tasks against the entity reuse rules (PLAN Step 4.5 item 3).

  python scripts/audit_diversity.py --tasks data/runs/depth0_partial.jsonl            # report
  python scripts/audit_diversity.py --tasks data/runs/depth0_partial.jsonl --apply    # keep first, drop rest

Rows are visited in file order; a task is kept unless it reuses an
(atomic action, entity) pair or pushes a user past the per-user limit. Dropped
rows are written next to the file as <name>.dropped.jsonl. No model calls.
"""
import argparse
import json
import os
from pathlib import Path

from pioneer.paths import TAU2_DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--max-per-user", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    from tau2.data_model.tasks import Task
    from pioneer.env import RetailSynthEnv
    from pioneer.initializer.diversity import EntityLedger
    from pioneer.schemas import TaskMeta

    env = RetailSynthEnv()
    env.init()
    keys = env.top_level_keys()
    ledger = EntityLedger(args.max_per_user)
    kept, dropped = [], []
    for line in args.tasks.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        task, meta = Task.model_validate(row["task"]), TaskMeta.model_validate(row["meta"])
        atomic = row.get("atomic_action") or task.evaluation_criteria.actions[0].name
        users, entities = ledger.entities_of(task, meta, env, keys)
        reason = ledger.violation(atomic, users, entities)
        entry = dict(task_id=task.id, atomic_action=atomic, users=sorted(users), entities=sorted(entities))
        if reason:
            dropped.append((row, dict(entry, reason=reason)))
        else:
            ledger.register(atomic, users, entities)
            kept.append((row, entry))
    print(json.dumps(dict(kept=[e for _, e in kept], dropped=[e for _, e in dropped]), indent=2, ensure_ascii=False))
    print(f"kept={len(kept)} dropped={len(dropped)}")
    if args.apply and dropped:
        dropped_path = args.tasks.with_name(args.tasks.stem + ".dropped.jsonl")
        dropped_path.write_text("".join(json.dumps(dict(r, dropped_reason=e["reason"]), ensure_ascii=False) + "\n"
                                        for r, e in dropped), encoding="utf-8")
        args.tasks.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r, _ in kept), encoding="utf-8")
        print(f"rewrote {args.tasks}; dropped rows saved to {dropped_path}")


if __name__ == "__main__":
    main()
