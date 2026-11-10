"""[INDEPENDENT] Agent construction and pending E8 are tested without networking."""
import json
import pytest
from pioneer.initializer.agent import InitializerAgent, normalize_binding_keys
from pioneer.initializer.requirements import WAIT_FOR_COMPLETION
from pioneer.schemas import Scenario, Intent, ExpectedOutcome
from pioneer.llm import MockLLM
from pioneer.paths import REPO_ROOT
from helpers.verifier_cases import candidate


def call(name, args, i):
    return dict(id=str(i), type="function", function=dict(name=name, arguments=json.dumps(args)))


@pytest.mark.parametrize("alias_keys", [False, True])
def test_initializer_queries_and_submits_a_program_checked_candidate(tmp_path, alias_keys):
    task, meta, *_ = candidate()
    if alias_keys:
        meta.argument_bindings = {f"action_{key}": value for key, value in meta.argument_bindings.items()}
    task.user_scenario.instructions.task_instructions += " " + WAIT_FOR_COMPLETION
    llm = MockLLM([dict(role="assistant", tool_calls=[call("query_data", dict(collection="orders", where=[["status", "eq", "pending"]], limit=1), 0)]),
                   dict(role="assistant", tool_calls=[call("submit_candidate", dict(task=task.model_dump(mode="json"), meta=meta.model_dump(mode="json")), 1)])])
    holdout = tmp_path / "users.json"
    holdout.write_text("[]")
    world = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
    scenario = Scenario(scenario_id="test", intents=[Intent(atomic_action="cancel_pending_order")], expected_outcome=ExpectedOutcome.COMPLETE)
    actual, grounded = InitializerAgent(llm, world, holdout).run(scenario, "new-id")
    assert actual.id == "new-id" and grounded.scenario_id == "test"
    assert grounded.instance_signature and grounded.verifier_report is None
    assert "0.order_id" in grounded.argument_bindings
    assert all(not key.startswith("action_") for key in grounded.argument_bindings)
    assert llm.usage["calls"] == 2


def test_binding_key_normalization_preserves_evidence_and_rejects_conflicts():
    expression = {"source": "read", "step": 2, "path": "record_id"}
    assert normalize_binding_keys({"action_0.record_id": expression})["0.record_id"] is expression
    with pytest.raises(ValueError, match="Conflicting.*0.record_id"):
        normalize_binding_keys({"0.record_id": expression,
                                "action_0.record_id": {"source": "text", "value": "other"}})


def test_initializer_budget_preserves_failure_instead_of_fake_pass(tmp_path):
    holdout = tmp_path / "users.json"
    holdout.write_text("[]")
    world = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
    scenario = Scenario(scenario_id="test", intents=[Intent(atomic_action="cancel_pending_order")], expected_outcome=ExpectedOutcome.COMPLETE)
    agent = InitializerAgent(MockLLM([dict(role="assistant", content="Done")]), world, holdout)
    with pytest.raises(RuntimeError, match="rework_queue"):
        agent.run(scenario, "test", max_turns=1)
