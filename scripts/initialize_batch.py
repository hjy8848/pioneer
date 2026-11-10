"""[INDEPENDENT] Isolate native conversation adapters in bounded worker processes."""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from pioneer.paths import REPO_ROOT
from pioneer.stats.ledger import summarize_ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--only", nargs="+", help="restrict to these atomic actions (default: all in the world model)")
    parser.add_argument("--known-tasks", type=Path,
                        help="jsonl of {task, meta} admitted earlier; counted for entity reuse limits only")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers must be 1..4")
    world = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    atoms = [a for a in world["atomic_actions"] if not args.only or a in args.only]
    if args.only and set(args.only) - set(atoms):
        raise ValueError(f"unknown atomic actions: {sorted(set(args.only) - set(atoms))}")
    pending, active, jobs = list(atoms), [], []
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
            command = [sys.executable, str(REPO_ROOT / "scripts/initialize.py"),
                "--out", str(folder.resolve()), "--atomic", atom, "--n", str(args.n),
                "--ledger-root", str(args.out.resolve())]
            if args.known_tasks:
                command += ["--known-tasks", str(args.known_tasks.resolve())]
            process = subprocess.Popen(command,
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
    seen, unique, demoted = set(), [], set()
    for row in accepted:
        signature = row["meta"]["instance_signature"]
        if signature in seen:
            demoted.add(id(row))
            queue.append(dict(candidate_id=row["candidate_id"], reason="global instance duplicate", previous=dict(task=row["task"], meta=row["meta"])))
        else:
            seen.add(signature)
            unique.append(row)
    stat_rows = [dict(r, passed=False) if id(r) in demoted else r for r in records]
    summary = summarize_ledger(stat_rows, atoms, requested=len(atoms) * args.n)
    summary.update(rework_queue=len(queue), jobs=jobs)
    (args.out / "depth0.jsonl").write_text("".join(json.dumps(dict(task=r["task"],meta=r["meta"]),ensure_ascii=False)+"\n" for r in unique),encoding="utf-8")
    (args.out / "rework_queue.json").write_text(json.dumps(queue,ensure_ascii=False,indent=2),encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary),flush=True)


if __name__ == "__main__":
    main()
