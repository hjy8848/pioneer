"""Stage 2 - Evolver + global coverage tracker (PLAN Step 5).

[BLOG] Start from every atomic action; each generation applies evolution
stressors to existing scenarios to produce harder descendants. A global tracker
steers each generation toward uncovered outcomes, including refusals.
"""

from pioneer.schemas import Scenario, Stressor


def atomic_scenarios(knowledge: dict) -> list[Scenario]:
    """Depth-0 scenarios, one family per atomic action. TODO(PLAN Step 4/5)."""
    raise NotImplementedError("PLAN Step 4")


def mutate(parent: Scenario, stressor: Stressor, knowledge: dict, coverage, llm) -> Scenario:
    """Apply one stressor to a verified parent scenario. TODO(PLAN Step 5)."""
    raise NotImplementedError("PLAN Step 5")
