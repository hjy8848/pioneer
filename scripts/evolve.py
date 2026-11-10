"""[BLOG] Stage 2 driver: evolve verified tasks into harder descendants, generation by generation.

  # what would the next generation work on (no model calls)
  python scripts/evolve.py --out data/runs/evolve1 --pool data/runs/depth0_partial.jsonl "data/runs/depth0_rest/shards/*/candidates.jsonl" --plan-only
  # run it: 2 generations of 10 candidates, 4 isolated worker processes
  python scripts/evolve.py --out data/runs/evolve1 --pool ... --generations 2 --per-generation 10 --workers 4

Each candidate is Evolver agent -> Initializer -> Verifier -> rework (src/pioneer/pipeline.py). One
worker process per candidate keeps the native conversation adapter single-threaded. Re-running resumes:
plans, scenarios and admitted candidates are kept; only unfinished candidates are attempted again.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from pioneer.paths import HOLDOUT_DIR, REPO_ROOT, TAU2_DATA_DIR

SCRIPT = Path(__file__).resolve()


def expand(patterns):
    files = []
    for pattern in patterns:
        files += [Path(p) for p in sorted(glob.glob(str(pattern)))]
    return files


def shard_dir(out: Path, cid: str) -> Path:
    path = out / "shards" / cid.replace(":", "_")
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_pool(args):
    from pioneer.evolver import load_pool
    return load_pool(expand(args.pool) + sorted((args.out / "shards").glob("*/candidates.jsonl")))


def worker(args, config):
    """Run one assignment end to end inside this process."""
    from pioneer.env import RetailSynthEnv
    from pioneer.evolver import EvolverAgent, parent_scenario
    from pioneer.initializer.contract import scenario_key
    from pioneer.initializer.diversity import EntityLedger
    from pioneer.llm import LiteLLMClient
    from pioneer.pipeline import run_rounds
    from pioneer.schemas import Scenario, Stressor

    assignment = json.loads(Path(args.worker).read_text(encoding="utf-8"))
    cid = assignment["cid"]
    folder = shard_dir(args.out, cid)
    ledger_path, scenario_path = folder / "candidates.jsonl", folder / "scenario.json"
    world = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
    params = dict(config["llm"], seed=config["seed"], cache_dir=REPO_ROOT / config["llm"]["cache_dir"],
                  api_base=os.environ["LITELLM_API_BASE"], api_key=os.environ["LITELLM_API_KEY"])
    pool = current_pool(args)
    history = [json.loads(s) for s in ledger_path.read_text(encoding="utf-8").splitlines()] if ledger_path.exists() else []
    if any(r.get("passed") for r in history):
        return print(f"{cid} already admitted", flush=True)
    parent_row = next(r for r in pool if r["task"]["id"] == assignment["parent_id"])
    parent = parent_scenario(parent_row)

    if scenario_path.exists():  # an interrupted candidate keeps its scenario; no second Evolver call
        scenario = Scenario.model_validate(json.loads(scenario_path.read_text(encoding="utf-8"))["scenario"])
    else:
        client = LiteLLMClient(**params)
        evolver = EvolverAgent(client, world, HOLDOUT_DIR / "users.json", config["evolution"]["max_depth"])
        scenario = evolver.run(parent_row, parent, Stressor(assignment["stressor"]), cid, assignment["gaps"])
        scenario_path.write_text(json.dumps(dict(scenario=scenario.model_dump(mode="json"), usage=client.usage,
                                                 trace=evolver.trace), ensure_ascii=False, indent=2), encoding="utf-8")

    env = RetailSynthEnv()
    env.init()
    max_per_user = config["evolution"].get("max_tasks_per_user", 8)

    def ledger():
        rows = [dict(r, atomic_action=scenario_key(parent_scenario(r))) for r in current_pool(args)]
        return EntityLedger.from_rows(rows, env, max_per_user)

    def append(record):
        with ledger_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    prior = history[-1] if history else {}
    record, previous, diagnosis = run_rounds(
        scenario, cid, knowledge=world, params=params, verifier_cfg=config["verifier"],
        seed_instances={r["meta"]["instance_signature"] for r in pool}, append=append, ledger_factory=ledger,
        rounds=config["rework"]["rounds_per_batch"], start_round=max((r["round"] for r in history), default=-1) + 1,
        parent=dict(task=parent_row["task"], meta={k: v for k, v in parent_row["meta"].items() if k != "verifier_report"}),
        previous=prior.get("proposal") or (dict(task=prior["task"], meta=prior["meta"]) if "task" in prior else None),
        diagnosis=prior.get("error") or prior.get("report"), atomic_action=scenario_key(scenario))
    queue = folder / "rework_queue.json"
    if record is None:
        queue.write_text(json.dumps([dict(candidate_id=cid, scenario=scenario.model_dump(mode="json"), previous=previous,
                                          diagnosis=diagnosis)], ensure_ascii=False, indent=2), encoding="utf-8")
    elif queue.exists():
        queue.unlink()


def driver(args, config):
    from pioneer.evolver import CoverageTracker
    from pioneer.stats.ledger import summarize_ledger

    world = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
    evolution = config["evolution"]
    args.out.mkdir(parents=True, exist_ok=True)
    generations = args.generations or evolution["generations"]
    per_generation = args.per_generation or evolution["candidates_per_generation"]
    for generation in range(1, generations + 1):
        tracker = CoverageTracker(world, evolution["max_depth"]).add_all(current_pool(args))
        plan_path = args.out / f"plan_g{generation}.json"
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        else:
            plan = dict(generation=generation, coverage=tracker.summary(),
                        assignments=[dict(a, cid=f"evo:g{generation}:s{a['slot']}", gaps=tracker.gaps())
                                     for a in tracker.plan(per_generation, f"{config['seed']}:g{generation}")])
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"generation {generation}: pool={plan['coverage']['tasks']} covered={plan['coverage']['covered']}", flush=True)
        if args.plan_only:
            for a in plan["assignments"]:
                print(f"  {a['cid']}: {a['stressor']} on {a['parent_id']}")
            return
        pending, active, failed = list(plan["assignments"]), [], False
        while (pending and not failed) or active:
            while pending and not failed and len(active) < args.workers:
                assignment = pending.pop(0)
                folder = shard_dir(args.out, assignment["cid"])
                file = folder / "assignment.json"
                file.write_text(json.dumps(assignment, ensure_ascii=False), encoding="utf-8")
                log = (folder / "run.log").open("a", encoding="utf-8")
                command = [sys.executable, str(SCRIPT), "--out", str(args.out.resolve()), "--worker", str(file.resolve()),
                           "--pool", *[str(p) for p in args.pool]]
                process = subprocess.Popen(command, cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=0x08000000 if os.name == "nt" else 0)
                active.append((assignment["cid"], process, log))
                print(f"started {assignment['cid']} pid={process.pid}", flush=True)
            for entry in active[:]:
                cid, process, log = entry
                if process.poll() is not None:
                    log.close()
                    active.remove(entry)
                    print(f"finished {cid} exit={process.returncode}", flush=True)
                    failed = failed or bool(process.returncode)  # inspect before more paid work
            if active:
                time.sleep(3)
        if failed:
            raise SystemExit("a worker failed; inspect shards/*/run.log, then re-run to resume")

    rows = [json.loads(s) for f in sorted((args.out / "shards").glob("*/candidates.jsonl"))
            for s in f.read_text(encoding="utf-8").splitlines() if s.strip()]
    tracker = CoverageTracker(world, evolution["max_depth"]).add_all(current_pool(args))
    summary = summarize_ledger(rows)
    summary["coverage"] = tracker.summary()
    summary["rework_queue"] = sum(len(json.loads(f.read_text(encoding="utf-8")))
                                  for f in (args.out / "shards").glob("*/rework_queue.json"))
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(dict(accepted=summary["accepted"], cost_usd=summary["cost_usd"], covered=summary["coverage"]["covered"])), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pool", nargs="+", required=True, help="jsonl files or globs of admitted {task, meta} rows")
    parser.add_argument("--generations", type=int)
    parser.add_argument("--per-generation", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--worker", help="(internal) assignment json to run in this process")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers must be 1..4")
    os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    config = yaml.safe_load((REPO_ROOT / "configs/retail.yaml").read_text(encoding="utf-8"))
    (worker if args.worker else driver)(args, config)


if __name__ == "__main__":
    main()
