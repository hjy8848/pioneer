"""[INDEPENDENT] Agent construction and pending E8 are tested without networking."""
import json
import pytest
from pioneer.initializer.agent import InitializerAgent
from pioneer.initializer.requirements import WAIT_FOR_COMPLETION
from pioneer.schemas import Scenario, Intent, ExpectedOutcome
from pioneer.llm import MockLLM
from pioneer.paths import REPO_ROOT
from helpers.verifier_cases import candidate


def call(name, args, i):
    return dict(id=str(i), type="function", function=dict(name=name, arguments=json.dumps(args)))


def test_initializer_queries_and_submits_a_program_checked_candidate(tmp_path):
    task, meta, *_ = candidate()
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
    assert llm.usage["calls"] == 2


def test_initializer_budget_preserves_failure_instead_of_fake_pass(tmp_path):
    holdout = tmp_path / "users.json"
    holdout.write_text("[]")
    world = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
    scenario = Scenario(scenario_id="test", intents=[Intent(atomic_action="cancel_pending_order")], expected_outcome=ExpectedOutcome.COMPLETE)
    agent = InitializerAgent(MockLLM([dict(role="assistant", content="Done")]), world, holdout)
    with pytest.raises(RuntimeError, match="rework_queue"):
        agent.run(scenario, "test", max_turns=1)
