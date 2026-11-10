"""[INDEPENDENT] Run Explorer through the shared cached LLM client."""
import argparse
import json
import os
from pathlib import Path

import yaml
from pioneer.paths import REPO_ROOT, TAU2_DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs/explorer.yaml")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data/knowledge/retail_world_model.json")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from pioneer.explorer.agent import ExplorerAgent
    from pioneer.llm import LiteLLMClient

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    max_turns = config.pop("max_turns", 24)
    if "shared_config" in config:
        common = yaml.safe_load((args.config.parent / config["shared_config"]).read_text(encoding="utf-8"))
        config = dict(common["llm"], seed=common["seed"])
    config["cache_dir"] = REPO_ROOT / config["cache_dir"]
    client = LiteLLMClient(**config, api_key=os.environ.get("LITELLM_API_KEY"))
    if args.smoke:
        print(json.dumps(client.json([{"role": "user", "content": 'Return only JSON: {"ok": true}'}])))
        print(json.dumps(client.usage))
    else:
        knowledge = ExplorerAgent(client).run(args.out, max_turns=max_turns)
        print(json.dumps(dict(rule_counts=knowledge["rule_counts"], llm_usage=knowledge["llm_usage"])))


if __name__ == "__main__":
    main()
