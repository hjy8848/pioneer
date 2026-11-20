"""[INDEPENDENT] Add environment-derived 'what it changes' to the world model (no model calls).

  python scripts/world_effects.py            # print the effects per action
  python scripts/world_effects.py --apply    # write them into actions[tool].observed_effects
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

from pioneer.paths import REPO_ROOT, TAU2_DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--world", type=Path, default=REPO_ROOT / "data/knowledge/retail_world_model.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    from pioneer.explorer.agent import ExplorerAgent
    from pioneer.explorer.effects import observed_effects, schema_enums
    from pioneer.llm import MockLLM

    world = json.loads(args.world.read_text(encoding="utf-8-sig"))
    with tempfile.TemporaryDirectory() as tmp:  # replays recorded cases; holdout filtering already applied then
        holdout = Path(tmp) / "holdout.json"
        holdout.write_text("[]", encoding="utf-8")
        agent = ExplorerAgent(MockLLM([]), holdout)
        effects, report = observed_effects(world, agent.case, agent.identifiers,
                                           schema_enums(agent.metadata["db_schema"]))
    for tool in sorted(world["actions"]):
        for item in effects.get(tool, []):
            print(f"{tool:32s} {item['effect']}   [{len(item['evidence'])} cases]")
    print(json.dumps(report))
    if args.apply:
        for tool, summary in world["actions"].items():
            summary["observed_effects"] = effects.get(tool, [])
        args.world.write_text(json.dumps(world, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"rewrote {args.world}")


if __name__ == "__main__":
    main()
