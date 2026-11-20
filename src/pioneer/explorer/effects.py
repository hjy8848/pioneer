"""[BLOG] The world model says "what each action ... changes".

[INDEPENDENT] The effects are derived from the environment, not written by a model:
every successful sandbox observation and every positive probe case is replayed in a
fresh environment, the database is compared before and after, and entity identifiers
are abstracted so effects read per action, e.g. ``orders.<id>.status: 'pending' ->
'cancelled'``. A replay whose resulting hash differs from the recorded one is skipped
and reported, so only reproduced effects enter the world model.
"""
from collections import defaultdict


def _diff(a, b, path):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            if a.get(key) != b.get(key):
                yield from _diff(a.get(key), b.get(key), path + [key])
    elif isinstance(a, list) and isinstance(b, list) and len(b) > len(a) and b[:len(a)] == a:
        yield path, "appended", b[len(a):]
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for index, (x, y) in enumerate(zip(a, b)):
            if x != y:
                yield from _diff(x, y, path + [index])
    else:
        yield path, a, b


def schema_enums(schema) -> set:
    """Every value listed in an ``enum`` of the database JSON schema (Literal fields)."""
    found = set()
    if isinstance(schema, dict):
        found |= {v for v in schema.get("enum", []) if isinstance(v, str)}
        for value in schema.values():
            found |= schema_enums(value)
    elif isinstance(schema, list):
        for value in schema:
            found |= schema_enums(value)
    return found


def _short(value, identifiers, enums=frozenset()):
    """Keep only schema enum values (statuses, reasons); abstract instance data."""
    if isinstance(value, str):
        return "<id>" if value in identifiers else repr(value) if value in enums else "<text>"
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, (int, float)):
        return "<number>"
    return "<value>"


def describe(path, before, after, identifiers, enums=frozenset()) -> str:
    where = ".".join("<id>" if isinstance(s, str) and s in identifiers else "*" if isinstance(s, int) else str(s)
                     for s in path)
    if before == "appended":
        fields = sorted({f"{k}={_short(v, identifiers, enums)}" for entry in after if isinstance(entry, dict)
                         for k, v in entry.items() if isinstance(v, str)})
        return f"{where}: {len(after)} entr{'y' if len(after) == 1 else 'ies'} appended" + (f" ({', '.join(fields)})" if fields else "")
    if isinstance(before, (int, float)) and isinstance(after, (int, float)) and not isinstance(before, bool):
        return f"{where}: {'increased' if after > before else 'decreased'}"
    if before is None:
        return f"{where}: set to {_short(after, identifiers, enums)}"
    if after is None:
        return f"{where}: cleared"
    return f"{where}: {_short(before, identifiers, enums)} -> {_short(after, identifiers, enums)}"


def observed_effects(world: dict, build_case, identifiers: set, enums=frozenset()):
    """({tool: [{effect, evidence}]}, report). ``build_case(tool, arguments, patches, setup)`` -> env."""
    sources = [(oid, obs["tool"], dict(arguments=obs["arguments"], patches=obs.get("patches") or (),
                                       setup=obs.get("setup") or ()), obs["execution"])
               for oid, obs in world.get("observations", {}).items()]
    for rule in world.get("rules", []):
        positive = rule["cases"][0]
        sources.append((rule["rule_id"], rule["tools"][0], dict(arguments=positive["case"]["arguments"],
                        patches=positive["case"].get("patches") or (), setup=positive["case"].get("setup") or ()),
                        positive["execution"]))
    effects, report = defaultdict(lambda: defaultdict(list)), dict(replayed=0, skipped_unchanged=0, mismatched=[])
    for evidence, tool, case, recorded in sources:
        if not recorded["ok"] or recorded["hash_before"] == recorded["hash_after"]:
            report["skipped_unchanged"] += 1
            continue
        env = build_case(tool, **case)
        before = env.state()["db"]
        result = env.execute(tool, case["arguments"])
        if not result.ok or result.hash_after != recorded["hash_after"]:
            report["mismatched"].append(evidence)
            continue
        report["replayed"] += 1
        for path, a, b in _diff(before, env.state()["db"], []):
            effects[tool][describe(path, a, b, identifiers, enums)].append(evidence)
    out = {tool: [dict(effect=text, evidence=sorted(set(ids))) for text, ids in sorted(found.items())]
           for tool, found in effects.items()}
    return out, report
