"""[INDEPENDENT] Remove instance-specific rules from the committed world model.

  python scripts/audit_world_model.py            # dry run, prints the report
  python scripts/audit_world_model.py --apply    # rewrites the world model

No model calls. Re-running after --apply is a no-op.
"""
import argparse
import json
import os
from pathlib import Path

from pioneer.paths import REPO_ROOT, TAU2_DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=REPO_ROOT / "data/knowledge/retail_world_model.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
    from pioneer.env import RetailSynthEnv
    from pioneer.explorer.audit import audit_world_model
    from pioneer.explorer.validation import collect_identifiers

    env = RetailSynthEnv()
    env.init()
    world = json.loads(args.world.read_text(encoding="utf-8-sig"))
    audited, report = audit_world_model(world, collect_identifiers(env), env.get_metadata()["policy"])
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.apply and report["removed"]:
        args.world.write_text(json.dumps(audited, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"rewrote {args.world}")


if __name__ == "__main__":
    main()
