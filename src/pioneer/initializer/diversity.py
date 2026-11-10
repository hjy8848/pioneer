"""[INDEPENDENT] Entity candidate pools and reuse limits.

Observed in the first Step 4 pilot: the Initializer takes the first rows a query
returns, so several tasks piled onto the same user and order. This module

* derives, from the verified world-model rules of a tool, which entity collection
  and constant constraints a task for that tool needs (no hand-written table);
* samples a seeded pool of eligible, not-yet-used entities for the agent;
* keeps a ledger of used (atomic action, entity) pairs and per-user task counts,
  which the Initializer's preflight enforces.

Domain-agnostic: collection names, key fields and identifier structure come from
the environment adapter.
"""
import random
from collections import Counter
from typing import Optional

from pioneer.verifier.grounding import identity_matches, instruction_text

ACTIVE = {"confirmed", "confirmed_by_tool", "enforced_by_tool", "policy_only"}


def _has_ref(value) -> bool:
    if isinstance(value, dict):
        return "ref" in value
    if isinstance(value, (list, tuple)):
        return any(_has_ref(v) for v in value)
    return False


def _plain_queries(check):
    """Conjunctive entity queries inside a check (``any`` is not a constraint)."""
    if not isinstance(check, dict):
        return
    if "collection" in check and "where" in check:
        yield check
    for member in check.get("all", []):
        yield from _plain_queries(member)


def derive_entity_constraints(world: dict, tool: str) -> dict[str, list]:
    """{collection: [constant where-clauses]} from the tool's verified state rules."""
    out: dict[str, list] = {}
    for rule in world.get("rules", []):
        if (rule.get("level") != "state" or tool not in rule["tools"] or rule.get("per_item")
                or rule.get("classification") not in ACTIVE):
            continue
        for query in _plain_queries(rule.get("check")):
            bucket = out.setdefault(query["collection"], [])
            for field, op, value in query["where"]:
                clause = [field, op, value]
                if "{" in field or _has_ref(value) or clause in bucket:
                    continue
                bucket.append(clause)
    return out


def _leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _leaves(v)


class EntityLedger:
    """Used (atomic action, entity) pairs and per-user task counts."""

    def __init__(self, max_tasks_per_user: int = 2):
        self.max_tasks_per_user = max_tasks_per_user
        self.pairs: set[tuple[str, str]] = set()
        self.users: Counter = Counter()

    # -- what a task touches -------------------------------------------------
    def entities_of(self, task, meta, env, keys=None) -> tuple[set, set]:
        """(user ids, "collection:id" entity keys) involved in a task.

        ``keys`` may carry a precomputed ``env.top_level_keys()`` (it dumps the
        whole database, so batch callers compute it once)."""
        leaves = set()
        for action in task.evaluation_criteria.actions or []:
            leaves |= set(_leaves(action.arguments))
        for step in meta.read_steps:
            for expr in step.get("arguments", {}).values():
                if isinstance(expr, dict) and expr.get("source") == "text":
                    leaves |= set(_leaves(expr.get("value")))
        users = set(identity_matches(env, instruction_text(task)[0]))
        entities = set()
        for collection, ids in (keys if keys is not None else env.top_level_keys()).items():
            key_field = env.KEY_FIELDS.get(collection)
            if not key_field:
                continue
            for leaf in leaves & ids:
                rows = env.query_data(collection, [(key_field, "eq", leaf)], limit=1)
                owner = leaf if collection == "users" else (rows[0].get("user_id") if rows else None)
                if owner is None:
                    continue  # shared catalog entities (e.g. products) may repeat freely
                users.add(owner)
                entities.add(f"{collection}:{leaf}")
        return users, entities

    # -- policy --------------------------------------------------------------
    def blocked(self, atomic_action: str, entity: str, user_id: Optional[str]) -> bool:
        return ((atomic_action, entity) in self.pairs
                or (user_id is not None and self.users[user_id] >= self.max_tasks_per_user))

    def violation(self, atomic_action: str, users: set, entities: set) -> Optional[str]:
        reused = sorted(e for e in entities if (atomic_action, e) in self.pairs)
        if reused:
            return f"{reused} already used for {atomic_action}; pick different entities"
        full = sorted(u for u in users if self.users[u] >= self.max_tasks_per_user)
        if full:
            return f"users {full} already appear in {self.max_tasks_per_user} tasks; pick different users"
        return None

    def register(self, atomic_action: str, users: set, entities: set) -> None:
        self.pairs |= {(atomic_action, e) for e in entities}
        for user in users:
            self.users[user] += 1

    @classmethod
    def from_rows(cls, rows, env, max_tasks_per_user: int = 2):
        """Rebuild from accepted ledger rows ({atomic_action, task, meta})."""
        from tau2.data_model.tasks import Task
        from pioneer.schemas import TaskMeta
        ledger = cls(max_tasks_per_user)
        keys = env.top_level_keys()
        for row in rows:
            task, meta = Task.model_validate(row["task"]), TaskMeta.model_validate(row["meta"])
            ledger.register(row["atomic_action"], *ledger.entities_of(task, meta, env, keys))
        return ledger


def compact(row: dict, limit: int = 8) -> dict:
    """Scalar top-level fields only, so the pool stays small."""
    scalars = {k: v for k, v in row.items() if isinstance(v, (str, int, float, bool))}
    return dict(list(scalars.items())[:limit])


def candidate_pool(env, world: dict, atomic_action: str, *, holdout, ledger: EntityLedger,
                   seed: str, size: int = 20) -> tuple[str, list[dict], int]:
    """Seeded sample of eligible, unused entities. Returns (collection, pool, eligible_count)."""
    constraints = {} if atomic_action.startswith("inform") else derive_entity_constraints(world, atomic_action)
    if not constraints:
        constraints = {"users": []}
    collection = sorted(constraints, key=lambda c: (-len(constraints[c]), c))[0]
    key_field = env.KEY_FIELDS[collection]
    eligible = []
    for row in env.query_data(collection, [tuple(c) for c in constraints[collection]]):
        owner = row.get("user_id")
        if owner in holdout:
            continue
        if ledger.blocked(atomic_action, f"{collection}:{row[key_field]}", owner):
            continue
        eligible.append(row)
    picked = random.Random(seed).sample(eligible, min(size, len(eligible)))
    return collection, [compact(r) for r in picked], len(eligible)
