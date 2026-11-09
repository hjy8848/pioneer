"""Six-interface environment wrapper around the native tau2-bench Retail domain.

Interface names follow the AWS blog (data-synthesis-v0, "Bring your own environment"):

    get_metadata()  [BLOG]  policy, tools, state schema
    init()          [BLOG]  fresh episode at a scenario's initial state
    run()           [BLOG]  user simulator <-> task agent, returns a multi-turn trajectory
    state()         [BLOG]  current internal state
    query_data()    [BLOG]  entities matching a predicate (grounds references in real values)
    reward()        [BLOG]  score a finished trajectory against evaluation criteria

The blog gives only names and one-line purposes; signatures here are
[INDEPENDENT]. ``execute()`` is an [INDEPENDENT] extension: the blog's ``run()``
is a whole conversation, while the Verifier needs to replay single reference
actions against a fresh environment.

tau2 business logic is never modified; everything goes through
``tau2.domains.retail.environment.get_environment`` [OFFICIAL].
"""

from __future__ import annotations

import operator
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from tau2.domains.retail.data_model import RetailDB
from tau2.domains.retail.environment import get_environment
from tau2.environment.environment import Environment
from tau2.environment.toolkit import get_tool_types


@dataclass
class ExecResult:
    """Outcome of executing one tool call against the live environment."""

    tool_name: str
    arguments: dict
    requestor: str
    ok: bool
    result: Any = None  # JSON-serialisable tool output when ok
    error: Optional[str] = None  # exception message when not ok
    hash_before: str = ""
    hash_after: str = ""

    @property
    def changed_state(self) -> bool:
        return self.hash_before != self.hash_after


# Predicate operators for query_data. Each takes (field_value, operand).
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "ge": operator.ge,
    "lt": operator.lt,
    "le": operator.le,
    "in": lambda v, xs: v in xs,
    "not_in": lambda v, xs: v not in xs,
    "contains": lambda v, x: v is not None and x in v,
    "len_eq": lambda v, n: v is not None and len(v) == n,
    "len_ge": lambda v, n: v is not None and len(v) >= n,
    "exists": lambda v, flag: (v is not None) == bool(flag),
}


def _get_path(obj: Any, path: str) -> Any:
    """Resolve a dotted path ("address.zip") on nested dicts; None if absent."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


class RetailSynthEnv:
    """Native tau2 Retail environment exposed through the six blog interfaces."""

    domain = "retail"
    COLLECTIONS = ("users", "orders", "products", "variants")

    def __init__(self) -> None:
        self._env: Optional[Environment] = None

    # ------------------------------------------------------------------ #
    # get_metadata
    # ------------------------------------------------------------------ #
    def get_metadata(self) -> dict:
        """Policy text, tool schemas (with ToolType), DB JSON schema and DB stats."""
        env = get_environment()
        tool_types = get_tool_types(env.tools)
        tools = []
        for tool in sorted(env.get_tools(), key=lambda t: t.name):
            tools.append(
                {
                    "name": tool.name,
                    "type": tool_types[tool.name].value,
                    "openai_schema": tool.openai_schema,
                }
            )
        return {
            "domain": self.domain,
            "policy": env.get_policy(),
            "tools": tools,
            "db_schema": RetailDB.model_json_schema(),
            "db_stats": self._db_stats(env.tools.db),
        }

    @staticmethod
    def _db_stats(db: RetailDB) -> dict:
        stats = dict(db.get_statistics())
        stats["available_items"] = sum(
            v.available for p in db.products.values() for v in p.variants.values()
        )
        stats["order_status"] = dict(Counter(o.status for o in db.orders.values()))
        stats["payment_sources"] = dict(
            Counter(
                pm.source
                for u in db.users.values()
                for pm in u.payment_methods.values()
            )
        )
        stats["orders_per_user"] = dict(
            sorted(Counter(len(u.orders) for u in db.users.values()).items())
        )
        return stats

    # ------------------------------------------------------------------ #
    # init / state
    # ------------------------------------------------------------------ #
    def init(self, task: Any = None) -> str:
        """Start a fresh episode (DB reloaded from disk). Returns the initial DB hash.

        If ``task`` (a tau2 ``Task``) has an ``initial_state``, it is applied with
        tau2's own ``Environment.set_state`` semantics.
        """
        self._env = get_environment()
        initial_state = getattr(task, "initial_state", None)
        if initial_state is not None:
            self._env.set_state(
                initialization_data=initial_state.initialization_data,
                initialization_actions=initial_state.initialization_actions,
                message_history=initial_state.message_history or [],
            )
        return self._env.get_db_hash()

    @property
    def env(self) -> Environment:
        if self._env is None:
            raise RuntimeError("call init() first")
        return self._env

    def state(self, include_db: bool = True) -> dict:
        """Current DB hash and (optionally) a full DB dump."""
        out = {"hash": self.env.get_db_hash()}
        if include_db:
            out["db"] = self.env.tools.db.model_dump()
        return out

    # ------------------------------------------------------------------ #
    # execute  [INDEPENDENT extension]
    # ------------------------------------------------------------------ #
    def execute(
        self, tool_name: str, arguments: Optional[dict] = None, requestor: str = "assistant"
    ) -> ExecResult:
        """Execute one tool call; never swallows the failure, reports it in ExecResult."""
        arguments = dict(arguments or {})
        before = self.env.get_db_hash()
        try:
            raw = self.env.make_tool_call(tool_name, requestor=requestor, **arguments)
            self.env.sync_tools()
            result = Environment.to_json_str(raw)
            ok, error = True, None
        except Exception as e:  # tau2 tools signal failures with ValueError
            result, ok, error = None, False, f"{type(e).__name__}: {e}"
        return ExecResult(
            tool_name=tool_name,
            arguments=arguments,
            requestor=requestor,
            ok=ok,
            result=result,
            error=error,
            hash_before=before,
            hash_after=self.env.get_db_hash(),
        )

    # ------------------------------------------------------------------ #
    # query_data
    # ------------------------------------------------------------------ #
    def _records(self, collection: str) -> Iterable[dict]:
        db = self.env.tools.db
        if collection == "users":
            yield from (u.model_dump() for u in db.users.values())
        elif collection == "orders":
            yield from (o.model_dump() for o in db.orders.values())
        elif collection == "products":
            yield from (p.model_dump() for p in db.products.values())
        elif collection == "variants":
            for p in db.products.values():
                for v in p.variants.values():
                    rec = v.model_dump()
                    rec["product_id"] = p.product_id
                    rec["product_name"] = p.name
                    yield rec
        else:
            raise ValueError(f"unknown collection {collection!r}; use one of {self.COLLECTIONS}")

    def query_data(
        self,
        collection: str,
        where: Optional[list[tuple[str, str, Any]]] = None,
        predicate: Optional[Callable[[dict], bool]] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Return entities of ``collection`` matching all ``where`` clauses (AND).

        ``where`` is a list of ``(dotted_path, op, operand)`` with ``op`` in
        ``_OPS``; it is the serialisable form an LLM can emit. ``predicate`` is an
        optional extra Python callable for in-process use. Read-only.
        """
        clauses = where or []
        for _, op, _ in clauses:
            if op not in _OPS:
                raise ValueError(f"unknown op {op!r}; use one of {sorted(_OPS)}")
        out = []
        for rec in self._records(collection):
            if all(_OPS[op](_get_path(rec, path), val) for path, op, val in clauses) and (
                predicate is None or predicate(rec)
            ):
                out.append(rec)
                if limit is not None and len(out) >= limit:
                    break
        return out

    # ------------------------------------------------------------------ #
    # run / reward  -- implemented in PLAN Step 1
    # ------------------------------------------------------------------ #
    def run(self, task: Any, agent_cfg: dict, user_cfg: dict) -> Any:
        """[BLOG] Orchestrate user simulator <-> task agent on ``task``; return the trajectory.

        TODO(PLAN Step 1): delegate to tau2's native runner
        (tau2.runner: run_single_task / TextRunConfig) with agent "llm_agent"
        (or "llm_agent_gt" for the end-to-end reachability check) and
        "user_simulator". Return the tau2 SimulationRun.
        """
        raise NotImplementedError("PLAN Step 1")

    def reward(self, task: Any, simulation: Any) -> Any:
        """[OFFICIAL] Score a finished trajectory with tau2's own evaluator.

        TODO(PLAN Step 1): tau2.evaluator.evaluator.evaluate_simulation(
        simulation, task, EvaluationType.ALL, solo_mode=False, domain="retail").
        Synthesised tasks must use reward_basis [DB] or [DB, COMMUNICATE] only.
        """
        raise NotImplementedError("PLAN Step 1")
