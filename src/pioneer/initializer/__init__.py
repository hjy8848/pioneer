"""Stage 3a - Initializer + repair loop (PLAN Step 4).

[BLOG] Turns each scenario into a concrete task: a user goal plus a reference
outcome tied to specific values in the environment state. Tasks that fail
verification are diagnosed and reworked until they pass, not thrown away.
"""

from pioneer.schemas import Scenario


def instantiate(scenario: Scenario, env, knowledge: dict, llm, seed: int):
    """Scenario -> (tau2 Task, TaskMeta). TODO(PLAN Step 4)."""
    raise NotImplementedError("PLAN Step 4")


def repair(task, meta, report, env, knowledge: dict, llm, max_rounds: int = 3):
    """Rework a failing task guided by the Verifier's failure codes. TODO(PLAN Step 4)."""
    raise NotImplementedError("PLAN Step 4")
