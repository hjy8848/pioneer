"""Stage 3a - Initializer + repair loop (PLAN Step 4).

[BLOG] Turns each scenario into a concrete task: a user goal plus a reference
outcome tied to specific values in the environment state. Tasks that fail
verification are diagnosed and reworked until they pass, not thrown away.
"""

from .agent import InitializerAgent
from pioneer.schemas import Scenario, Intent
from pioneer.paths import HOLDOUT_DIR


def instantiate(scenario: Scenario, env, knowledge: dict, llm, seed: int):
    """Generate a native Task and TaskMeta from a scenario."""
    return InitializerAgent(llm, knowledge, HOLDOUT_DIR / "users.json").run(scenario, f"{scenario.scenario_id}:{seed}")


def repair(task, meta, report, env, knowledge: dict, llm, max_rounds: int = 3):
    """Generate one revision from Verifier diagnostics; the batch owns retries."""
    target = task.evaluation_criteria.actions[0].name if task.evaluation_criteria.actions else "inform:answer"
    scenario = Scenario(scenario_id=meta.scenario_id, intents=[Intent(atomic_action=target)], expected_outcome=meta.expected_outcome)
    return InitializerAgent(llm, knowledge, HOLDOUT_DIR / "users.json").run(scenario, task.id,
        previous=dict(task=task.model_dump(mode="json"), meta=meta.model_dump(mode="json")),
        diagnosis=report.model_dump(mode="json"))
