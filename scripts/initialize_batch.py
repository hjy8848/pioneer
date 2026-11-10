"""[INDEPENDENT] Isolate native conversation adapters in bounded worker processes."""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from pioneer.paths import REPO_ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--n", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers must be 1..4")
    world = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    pending, active, jobs = list(world["atomic_actions"]), [], []
    # Preserve a completed single-candidate rehearsal when expanding the batch.
    cancel = args.out / "shards/cancel_pending_order"
    if (args.out / "candidates.jsonl").exists() and not cancel.exists():
        cancel.mkdir(parents=True)
        for name in ("candidates.jsonl", "depth0.jsonl", "rework_queue.json"):
            source = args.out / name
            if source.exists():
                (cancel / name).write_bytes(source.read_bytes())
    while pending or active:
        while pending and len(active) < args.workers:
            atom = pending.pop(0)
            slug = re.sub(r"[^A-Za-z0-9_-]", "_", atom)
            folder = args.out / "shards" / slug
            folder.mkdir(parents=True, exist_ok=True)
            log = (folder / "run.log").open("a", encoding="utf-8")
            process = subprocess.Popen([sys.executable, str(REPO_ROOT / "scripts/initialize.py"),
                "--out", str(folder.resolve()), "--atomic", atom, "--n", str(args.n)],
                cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=0x08000000 if os.name == "nt" else 0)
            active.append((atom, folder, process, log))
            print(f"Started {atom} pid={process.pid}", flush=True)
        for entry in active[:]:
            atom, folder, process, log = entry
            if process.poll() is not None:
                log.close()
                jobs.append(dict(atomic_action=atom, exit_code=process.returncode))
                print(f"Finished {atom} exit={process.returncode}", flush=True)
                active.remove(entry)
                if process.returncode:
                    pending.clear()  # inspect failures before dispatching more paid work
        if active:
            time.sleep(3)
    records, queue = [], []
    for folder in (args.out / "shards").iterdir():
        ledger = folder / "candidates.jsonl"
        if ledger.exists():
            records.extend(json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines())
        if (folder / "rework_queue.json").exists():
            queue.extend(json.loads((folder / "rework_queue.json").read_text(encoding="utf-8")))
    accepted = [r for r in records if r.get("passed")]
    # Admission across shards still requires globally unique instances.
    seen, unique = set(), []
    for row in accepted:
        signature = row["meta"]["instance_signature"]
        if signature in seen:
            queue.append(dict(candidate_id=row["candidate_id"], reason="global instance duplicate", previous=dict(task=row["task"], meta=row["meta"])))
        else:
            seen.add(signature)
            unique.append(row)
    total = sum(r["llm_usage"]["cost_usd"] for r in records)
    summary = dict(accepted=len(unique), requested=len(world["atomic_actions"])*args.n, rework_queue=len(queue), jobs=jobs,
        initial_generation_cost_usd=sum(r["generation_usage"].get("cost_usd",0) for r in records if r["round"]==0),
        initial_validation_cost_usd=sum(r["validation_usage"].get("cost_usd",0) for r in records if r["round"]==0),
        rework_cost_usd=sum(r["llm_usage"].get("cost_usd",0) for r in records if r["round"]>0), total_cost_usd=total,
        average_cost_per_accepted=total/len(unique) if unique else None,
        projected_600_cost_usd=total/len(unique)*600 if unique else None,
        by_atomic={a:dict(candidates=len({r["candidate_id"] for r in records if r["atomic_action"]==a}),
            first_pass=sum(r["atomic_action"]==a and r["round"]==0 for r in unique),
            after_rework=sum(r["atomic_action"]==a and r["round"]>0 for r in unique)) for a in world["atomic_actions"]},
        failure_codes=dict(Counter(c for r in records for c in r.get("report",{}).get("failure_codes",[]))))
    (args.out / "depth0.jsonl").write_text("".join(json.dumps(dict(task=r["task"],meta=r["meta"]),ensure_ascii=False)+"\n" for r in unique),encoding="utf-8")
    (args.out / "rework_queue.json").write_text(json.dumps(queue,ensure_ascii=False,indent=2),encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary),flush=True)


if __name__ == "__main__":
    main()
