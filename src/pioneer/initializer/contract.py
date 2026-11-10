"""[INDEPENDENT] What reference actions a scenario may carry, by depth and outcome.

Depth 0 keeps the Step 4 contract unchanged. Deeper scenarios (made by the
Evolver) may chain several actions, refuse, or branch, so the contract is stated
against the scenario's intents and expected outcome.
"""
from collections import Counter

from pioneer.schemas import ExpectedOutcome, Scenario


def is_inform(atomic_action: str) -> bool:
    return atomic_action.startswith("inform")


def intent_tools(scenario: Scenario) -> list[str]:
    """Tools the scenario's intents ask for, in order (inform intents need none)."""
    return [i.atomic_action for i in scenario.intents if not is_inform(i.atomic_action)]


def scenario_key(scenario: Scenario) -> str:
    """Ledger key replacing the single atomic action used at depth 0."""
    if scenario.depth == 0:
        return scenario.intents[0].atomic_action
    return "|".join(i.atomic_action for i in scenario.intents) + "@" + scenario.expected_outcome.value


def check_actions(scenario: Scenario, actual: list[str]) -> None:
    """Raise ValueError when the reference action names do not fit the scenario."""
    if scenario.depth == 0:
        target = scenario.intents[0].atomic_action
        expected = [] if is_inform(target) else [target]
        if actual != expected:
            raise ValueError(f"reference tools must be {expected}")
        return
    tools, outcome = intent_tools(scenario), scenario.expected_outcome
    if outcome in (ExpectedOutcome.REFUSE, ExpectedOutcome.INFORM):
        ok, need = not actual, "no reference actions (nothing may change)"
    elif outcome == ExpectedOutcome.PARTIAL:
        ok = bool(actual) and not (Counter(actual) - Counter(tools)) and len(actual) < len(tools)
        need = f"a non-empty strict subset of {tools} (the compliant part only)"
    elif outcome == ExpectedOutcome.TRANSFER:
        ok, need = "transfer_to_human_agents" in actual, "a transfer_to_human_agents action"
    elif scenario.conditions:
        ok = bool(actual) and not (Counter(actual) - Counter(tools))
        need = f"only actions from {tools} (the branch the data selects)"
    else:
        ok, need = actual == tools, f"exactly {tools} in this order"
    if not ok:
        raise ValueError(f"reference tools must be {need}; got {actual}")
