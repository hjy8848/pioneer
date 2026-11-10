"""[INDEPENDENT] Verify a supplied synthetic Task + TaskMeta through E1-E9."""
import argparse
import json
import os
from pathlib import Path

import yaml
from pioneer.paths import REPO_ROOT, TAU2_DATA_DIR, HOLDOUT_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSON object with task and meta")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs/retail.yaml")
    parser.add_argument("--world", type=Path, default=REPO_ROOT / "data/knowledge/retail_world_model.json")
    args = parser.parse_args()
    os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from tau2.data_model.tasks import Task
    from pioneer.llm import LiteLLMClient
    from pioneer.schemas import TaskMeta
    from pioneer.verifier import verify

    load = lambda p: json.loads(p.read_text(encoding="utf-8"))
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    client_config = dict(config["llm"], seed=config["seed"])
    client_config["cache_dir"] = REPO_ROOT / client_config["cache_dir"]
    client_config["api_base"] = os.environ.get("LITELLM_API_BASE")
    if not client_config["api_base"]:
        raise ValueError("set LITELLM_API_BASE in the invoking process")
    client = LiteLLMClient(**client_config, api_key=os.environ.get("LITELLM_API_KEY"))
    candidate = load(args.input)
    task, meta = Task.model_validate(candidate["task"]), TaskMeta.model_validate(candidate["meta"])
    report = verify(task, meta, load(args.world), llm=client, holdout=load(HOLDOUT_DIR / "users.json"),
        official_signatures=load(HOLDOUT_DIR / "signatures.json"), trials=config["verifier"]["e2e_trials"],
        seed=config["seed"], max_steps=config["verifier"].get("max_steps", 60))
    output = dict(task_id=task.id, report=report.model_dump(mode="json"), llm_usage=client.usage,
                  llm_requests=client.records, llm_config=dict(client.params, api_base="<env:LITELLM_API_BASE>"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(task_id=task.id, passed=report.passed,
        failure_codes=[c.value for c in report.failure_codes], llm_usage=client.usage)))


if __name__ == "__main__":
    main()
