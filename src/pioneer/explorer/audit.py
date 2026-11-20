"""[INDEPENDENT] Deterministic audit of a committed world model.

Moves rules whose ``check`` hard-codes entity identifiers (true for one probe
case only) out of the active rule set, repairs the action claims that cited
them, and recomputes counts and policy coverage. Nothing is hand-edited and the
removed rules stay in ``discarded_rules`` with the reason, so the step is
reproducible and reversible from git history.
"""
from collections import Counter
from copy import deepcopy

from .coverage import policy_coverage
from .validation import accepted_calls, failing_calls, instance_identifiers

ACTIVE = {"confirmed_by_tool", "policy_only"}


def audit_world_model(world: dict, identifiers: set, policy: str, build_case=None):
    """Return (audited_world, report). ``world`` is not modified.

    With ``build_case`` (tool, arguments, patches, setup -> env) a tool-enforced rule whose
    check is false on a call the tool accepted is also removed as ``over_broad``.
    """
    world = deepcopy(world)
    removed, kept = {}, []
    for rule in world["rules"]:
        bad = instance_identifiers(rule.get("check"), identifiers)
        if bad:
            removed[rule["rule_id"]] = dict(rule, audit=dict(
                reason="check hard-codes entity identifiers", identifiers=bad,
                previous_classification=rule["classification"]), classification="instance_specific")
        else:
            kept.append(rule)
    if build_case is not None:
        for rule in list(kept):
            if rule["classification"] != "confirmed_by_tool":
                continue  # a policy-only rule is expected to fail on some calls the tool accepts
            failing = failing_calls(rule, accepted_calls(rule["tools"][0], world.get("observations", {}), kept), build_case)
            if failing:
                kept.remove(rule)
                removed[rule["rule_id"]] = dict(rule, audit=dict(
                    reason="check is false on calls the tool accepted", calls=failing,
                    previous_classification=rule["classification"]), classification="over_broad")
    by_id = {r["rule_id"]: r for r in kept}
    dropped_claims = []
    for tool, summary in world["actions"].items():
        for kind in ("requires", "failure_modes"):
            survivors = []
            for claim in summary.get(kind, []):
                ids = claim.get("evidence", [])
                alive = [i for i in ids if i not in removed]
                if len(alive) == len(ids):
                    survivors.append(claim)
                elif alive and all(i in by_id for i in alive):
                    claim["evidence"] = alive
                    claim["text"] = "; ".join(by_id[i]["text"] for i in alive)
                    claim["verified_checks"] = [by_id[i]["check"] for i in alive]
                    claim["classifications"] = [by_id[i]["classification"] for i in alive]
                    survivors.append(claim)
                else:
                    dropped_claims.append(dict(tool=tool, kind=kind, evidence=ids))
            summary[kind] = survivors
    world["rules"] = kept
    world["discarded_rules"] = world.get("discarded_rules", []) + list(removed.values())
    world["rule_counts"] = dict(Counter(r["classification"] for r in world["rules"] + world["discarded_rules"]))
    world["policy_coverage"] = policy_coverage(policy, kept, world["conversation_rules"],
                                               world.get("paragraph_explanations"))
    report = dict(removed=sorted(removed), dropped_claims=dropped_claims,
                  rule_counts=world["rule_counts"],
                  coverage=dict(covered=world["policy_coverage"]["covered"], total=world["policy_coverage"]["total"]))
    if removed:
        world.setdefault("audit", []).append(dict(
            kind="rule_audit", removed={k: v["classification"] for k, v in sorted(removed.items())},
            dropped_claims=dropped_claims))
    return world, report
