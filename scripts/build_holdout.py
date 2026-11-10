"""[INDEPENDENT] Extract isolation users and signatures from the official base split.

This is the only generation-side entry point allowed to read official tasks.
Matching uses [OFFICIAL] DB identities and tool types, never an LLM.
"""

import argparse
import json
import re
from pathlib import Path

from pioneer.paths import HOLDOUT_DIR, TAU2_DATA_DIR


def words(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def extract_holdout(tasks, base_ids, db, write_tools):
    """Resolve every base task; fail rather than silently omit an unknown identity."""
    by_id = {str(task["id"]): task for task in tasks}
    if len(set(base_ids)) != len(base_ids):
        raise ValueError("duplicate base task IDs")
    users, signatures, evidence = set(), [], {}
    for task_id in base_ids:
        task = by_id[str(task_id)]
        found = set()
        actions = task["evaluation_criteria"]["actions"] or []
        for action in actions:
            args = action["arguments"]
            if "user_id" in args:
                uid = args["user_id"]
                if uid not in db["users"]:
                    raise ValueError(f"unknown user in task {task_id}: {uid}")
                found.add(uid)
            # [OFFICIAL] Some reference READs deliberately query nonexistent orders.
            if args.get("order_id") in db["orders"]:
                found.add(db["orders"][args["order_id"]]["user_id"])
            for uid, user in db["users"].items():
                if args.get("email", "").casefold() == user["email"].casefold():
                    found.add(uid)
                if (args.get("first_name", "").casefold() == user["name"]["first_name"].casefold()
                        and args.get("last_name", "").casefold() == user["name"]["last_name"].casefold()
                        and args.get("zip") == user["address"]["zip"]):
                    found.add(uid)
        instructions = task["user_scenario"]["instructions"]
        known = (instructions.get("known_info", "") or "") if isinstance(instructions, dict) else instructions
        emails = {e.casefold() for e in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", known)}
        normalized = f" {words(known)} "
        for uid, user in db["users"].items():
            name = words(user["name"]["first_name"] + " " + user["name"]["last_name"])
            if (re.search(r"(?<!\w)" + re.escape(uid) + r"(?!\w)", known, re.IGNORECASE) or
                    user["email"].casefold() in emails or
                    (f" {name} " in normalized and
                     re.search(r"(?<!\d)" + re.escape(user["address"]["zip"]) + r"(?!\d)", known))):
                found.add(uid)
        if not found:
            raise ValueError(f"unresolved identity in base task {task_id}")
        users.update(found)
        evidence[str(task_id)] = sorted(found)
        sequence = [a["name"] for a in actions if a["name"] in write_tools]
        signatures.extend({"task_id": str(task_id), "user_id": uid, "write_actions": sequence}
                          for uid in sorted(found))
    return sorted(users), signatures, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HOLDOUT_DIR)
    args = parser.parse_args()
    retail = TAU2_DATA_DIR / "tau2" / "domains" / "retail"
    load = lambda name: json.loads((retail / name).read_text(encoding="utf-8"))
    # Import only after selecting the pinned data directory.
    import os
    os.environ["TAU2_DATA_DIR"] = str(TAU2_DATA_DIR)
    from tau2.domains.retail.environment import get_environment
    from tau2.environment.toolkit import ToolType, get_tool_types

    write_tools = {name for name, kind in get_tool_types(get_environment().tools).items()
                   if kind == ToolType.WRITE}
    base = load("split_tasks.json")["base"]
    if len(base) != 114:
        raise ValueError(f"expected 114 base tasks, got {len(base)}")
    users, signatures, evidence = extract_holdout(load("tasks.json"), base, load("db.json"), write_tools)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, value in (("users.json", users), ("signatures.json", signatures),
                        ("task_users.json", evidence)):
        (args.out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"base_tasks={len(base)} holdout_users={len(users)} signatures={len(signatures)}")


if __name__ == "__main__":
    main()
