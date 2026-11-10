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


def collect_identifiers(env) -> set[str]:
    """All entity identifiers (top-level keys and nested opaque keys) of the DB."""
    ids = set(env.opaque_identifiers())
    for keys in env.top_level_keys().values():
        ids |= keys
    return ids
