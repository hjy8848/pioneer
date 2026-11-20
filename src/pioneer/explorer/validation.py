"""[INDEPENDENT] Reject rule checks that hard-code instance identifiers.

A probe is run on one concrete case. If the Explorer writes that case's entity
id into the rule's check (``payment_method_id eq "<some card id>"``), the rule
is only true for that instance; used by the Verifier it would force every task
of the tool onto that one entity. Entity facts must be expressed with ``ref``s.
Domain-agnostic: the identifier set comes from the environment adapter.
"""
from typing import Iterator


def literal_strings(check) -> Iterator[str]:
    """String literals in a check, excluding ``{"ref": ...}`` references."""
    if isinstance(check, dict):
        if set(check) == {"ref"}:
            return
        if "where" in check:
            for clause in check["where"]:
                yield from literal_strings(clause[2])
            return
        if "compare" in check:
            left, _, right = check["compare"]
            yield from literal_strings(left)
            yield from literal_strings(right)
            return
        for key in ("all", "any"):
            for member in check.get(key, []):
                yield from literal_strings(member)
    elif isinstance(check, (list, tuple)):
        for member in check:
            yield from literal_strings(member)
    elif isinstance(check, str):
        yield check


def instance_identifiers(check, identifiers) -> list[str]:
    """Literals in ``check`` that equal a real entity identifier of the database."""
    return sorted({s for s in literal_strings(check) if s in identifiers})


def accepted_calls(tool: str, observations: dict, rules) -> list:
    """(evidence id, case) for every recorded call of ``tool`` that the tool executed successfully."""
    def case(c):
        return dict(arguments=c["arguments"], patches=c.get("patches") or (), setup=c.get("setup") or ())
    calls = [(oid, case(o)) for oid, o in observations.items() if o["tool"] == tool and o["execution"]["ok"]]
    for rule in rules:
        if tool in rule["tools"]:
            for index, probe_case in enumerate(rule.get("cases", [])):
                if probe_case["execution"]["ok"]:
                    calls.append((f"{rule['rule_id']}#{index}", case(probe_case["case"])))
    return calls


def failing_calls(rule: dict, calls, build_case) -> list[str]:
    """Calls the tool accepted on which ``rule``'s check is false or cannot be evaluated.

    A rule the tool enforces must hold on every call the tool accepted; otherwise the
    check is broader than the condition it describes, and E2 would reject legal tasks.
    """
    from .rules import check_rule
    bad = []
    for evidence, case in calls:
        env = build_case(rule["tools"][0], **case)
        try:
            holds = check_rule(env, rule, case["arguments"])
        except (ValueError, KeyError, TypeError, IndexError, StopIteration, AttributeError):
            holds = False  # E2 treats an unevaluable check as a violation, so it counts here too
        if not holds:
            bad.append(evidence)
    return bad


def collect_identifiers(env) -> set[str]:
    """All entity identifiers (top-level keys and nested opaque keys) of the DB."""
    ids = set(env.opaque_identifiers())
    for keys in env.top_level_keys().values():
        ids |= keys
    return ids
