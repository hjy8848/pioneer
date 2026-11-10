"""[BLOG] Depth-zero generation and rework; [INDEPENDENT] persistent batch ledger."""
import argparse
import json
import os
from pathlib import Path
import yaml
from pioneer.paths import REPO_ROOT, HOLDOUT_DIR, TAU2_DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--atomic")
    parser.add_argument("--n", type=int)
    parser.add_argument("--ledger-root", type=Path,
                        help="batch root whose shards/*/candidates.jsonl define already-used entities (default: --out)")
    parser.add_argument("--known-tasks", type=Path,
                        help="jsonl of {task, meta} admitted earlier; counted for entity reuse limits only")
    args = parser.parse_args()
    os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from pioneer.initializer import InitializerAgent
    from pioneer.llm import LiteLLMClient
    from pioneer.schemas import Scenario, Intent, ExpectedOutcome
    from pioneer.verifier import verify
    from pioneer.env import RetailSynthEnv
    from pioneer.initializer.diversity import EntityLedger
    from pioneer.stats.ledger import summarize_ledger
    config = yaml.safe_load((REPO_ROOT / "configs/retail.yaml").read_text(encoding="utf-8"))
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    knowledge = load(REPO_ROOT / "data/knowledge/retail_world_model.json")
    params = dict(config["llm"], seed=config["seed"], cache_dir=REPO_ROOT / config["llm"]["cache_dir"],
                  api_base=os.environ["LITELLM_API_BASE"], api_key=os.environ["LITELLM_API_KEY"])
    args.out.mkdir(parents=True, exist_ok=True)
    ledger_path = args.out / "candidates.jsonl"
    history = [json.loads(s) for s in ledger_path.read_text(encoding="utf-8").splitlines()] if ledger_path.exists() else []
    accepted = [r for r in history if r.get("passed")]
    seen = {r["meta"]["instance_signature"] for r in accepted}
    complete = {r["candidate_id"] for r in accepted}
    queue_path = args.out / "rework_queue.json"
    queued = load(queue_path) if queue_path.exists() else []
    atoms = [a for a in knowledge["atomic_actions"] if not args.atomic or a == args.atomic]
    if not atoms:
        raise ValueError("unknown atomic action")
    count = args.n or config["evolution"]["depth0_per_atomic_action"]
    def append(record):
        with ledger_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        history.append(record)

    diversity = config.get("diversity", {})
    ledger_root = args.ledger_root or args.out

    def entity_ledger():
        """Rebuilt before every candidate so sibling shard workers' admissions count."""
        files = [ledger_path] + sorted((ledger_root / "shards").glob("*/candidates.jsonl"))
        rows, seen_ids = [], set()
        for file in files:
            if not file.exists() or file.resolve() in seen_ids:
                continue
            seen_ids.add(file.resolve())
            rows += [r for r in map(json.loads, file.read_text(encoding="utf-8").splitlines()) if r.get("passed")]
        if args.known_tasks and args.known_tasks.exists():
            for line in args.known_tasks.read_text(encoding="utf-8-sig").splitlines():
                if line.strip():
                    known = json.loads(line)
                    rows.append(dict(known, atomic_action=known.get("atomic_action")
                                     or known["task"]["evaluation_criteria"]["actions"][0]["name"]))
        env = RetailSynthEnv()
        env.init()
        return EntityLedger.from_rows(rows, env, diversity.get("max_tasks_per_user", 2))
    for atom in atoms:
        for index in range(count):
            cid = f"depth0:{atom}:{index}"
            if cid in complete:
                continue
            outcome = ExpectedOutcome.INFORM if atom.startswith("inform:") else ExpectedOutcome.TRANSFER if atom == "transfer_to_human_agents" else ExpectedOutcome.COMPLETE
            scenario = Scenario(scenario_id=cid, intents=[Intent(atomic_action=atom)], expected_outcome=outcome)
            prior = [r for r in history if r["candidate_id"] == cid]
            prior_queue = next((q for q in queued if q["candidate_id"] == cid), {})
            previous = prior_queue.get("previous") or (dict(task=prior[-1]["task"], meta=prior[-1]["meta"]) if prior and "task" in prior[-1] else None)
            diagnosis = prior_queue.get("diagnosis") or (prior[-1].get("report", prior[-1].get("error")) if prior else None)
            queued = [q for q in queued if q["candidate_id"] != cid]
            start_round = max((r["round"] for r in prior), default=-1) + 1
            for round_no in range(start_round, start_round + config["rework"]["rounds_per_batch"]):
                client = LiteLLMClient(**params)
                initializer = InitializerAgent(client, knowledge, HOLDOUT_DIR / "users.json")
                generation = None
                print(f"{cid} round={round_no} generating", flush=True)
                try:
                    task, meta = initializer.run(scenario, cid, previous=previous, diagnosis=diagnosis, seen_instance=seen,
                        ledger=entity_ledger(), pool_size=diversity.get("pool_size", 20), seed=config["seed"])
                    generation = dict(client.usage)
                    meta.repair_rounds = round_no
                    report = verify(task, meta, knowledge, llm=client, seen_instance=seen,
                        trials=config["verifier"]["e2e_trials"], seed=config["seed"])
                    validation = {k:client.usage[k] - generation[k] for k in generation}
                    record = dict(candidate_id=cid, atomic_action=atom, round=round_no, passed=report.passed,
                        task=task.model_dump(mode="json"), meta=meta.model_dump(mode="json"), report=report.model_dump(mode="json"),
                        generation_usage=generation, validation_usage=validation, llm_usage=client.usage, initializer_trace=initializer.trace,
                        preflight_submissions=initializer.submissions)
                    append(record)
                    print(f"{cid} passed={report.passed} codes={[c.value for c in report.failure_codes]}", flush=True)
                    if report.passed:
                        accepted.append(record)
                        seen.add(meta.instance_signature)
                        complete.add(cid)
                        break
                    previous = dict(task=record["task"], meta=record["meta"])
                    diagnosis = report.model_dump(mode="json")
                except Exception as exc:
                    previous = initializer.last_proposal or previous
                    diagnosis = dict(error=f"{type(exc).__name__}: {exc}",
                        last_tool_result=initializer.trace[-1].get("response") if initializer.trace else None)
                    append(dict(candidate_id=cid, atomic_action=atom, round=round_no, passed=False,
                        error=diagnosis, proposal=previous, generation_usage=generation or dict(client.usage),
                        validation_usage={k:client.usage[k]-generation[k] for k in generation} if generation else {},
                        llm_usage=dict(client.usage), initializer_trace=initializer.trace,
                        preflight_submissions=initializer.submissions))
                    print(f"{cid} {diagnosis}", flush=True)
                    if type(exc).__name__ in {"RateLimitError", "AuthenticationError", "APIConnectionError"}:
                        raise
            if cid not in complete:
                queued.append(dict(candidate_id=cid, scenario=scenario.model_dump(mode="json"), previous=previous, diagnosis=diagnosis))
            (args.out / "depth0.jsonl").write_text("".join(json.dumps(dict(task=r["task"], meta=r["meta"]), ensure_ascii=False)+"\n" for r in accepted), encoding="utf-8")
            (args.out / "rework_queue.json").write_text(json.dumps(queued, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = summarize_ledger(history, atoms, requested=len(atoms) * count)
    summary["rework_queue"] = len(queued)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
