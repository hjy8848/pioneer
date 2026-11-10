"""[INDEPENDENT] Offline integration checks using native runner and evaluator."""

import importlib.util

import pytest
from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.data_model.tasks import Action, EvaluationCriteria, RewardType, Task, UserScenario

from pioneer.env import RetailSynthEnv
from pioneer.paths import REPO_ROOT


def make_task(actions):
    return Task(id="synthetic-step1", user_scenario=UserScenario(instructions="Say goodbye."),
                evaluation_criteria=EvaluationCriteria(actions=actions, reward_basis=[RewardType.DB]))


def cancel_action(env):
    order = env.query_data("orders", [("status", "eq", "pending")], limit=1)[0]
    return Action(action_id="cancel", name="cancel_pending_order",
                  arguments={"order_id": order["order_id"], "reason": "no longer needed"})


def replay(task, actions):
    env = RetailSynthEnv()
    env.init(task)
    messages = []
    for action in actions:
        call = ToolCall(id=action.action_id, name=action.name, arguments=action.arguments)
        messages.extend([AssistantMessage(role="assistant", tool_calls=[call]),
                         env.env.get_response(call)])
    return SimulationRun(id="replay", task_id=task.id, start_time="test", end_time="test",
                         duration=0, termination_reason=TerminationReason.USER_STOP, messages=messages)


def test_write_reward_requires_matching_state():
    env = RetailSynthEnv()
    env.init()
    action = cancel_action(env)
    task = make_task([action])
    assert env.reward(task, replay(task, [action])).reward == 1
    assert env.reward(task, replay(task, [])).reward == 0


def test_empty_actions_reject_unexpected_write():
    env = RetailSynthEnv()
    env.init()
    task = make_task([])
    assert env.reward(task, replay(task, [])).reward == 1
    assert env.reward(task, replay(task, [cancel_action(env)])).reward == 0


@pytest.mark.parametrize("agent", ["llm_agent", "llm_agent_gt"])
def test_native_runner_with_mock_llm(monkeypatch, agent):
    import tau2.agent.llm_agent as agent_module
    import tau2.user.user_simulator as user_module
    import tau2.runner.helpers as helpers

    def forbidden(*args, **kwargs):
        raise AssertionError("official task loading is forbidden")

    for name in ("load_tasks", "load_task_splits", "get_tasks"):
        monkeypatch.setattr(helpers, name, forbidden)
    user_turns = iter(["Please cancel my order.", "###STOP###"])

    def mock_user(**kwargs):
        return AssistantMessage(role="assistant", content=next(user_turns), cost=0)

    env = RetailSynthEnv()
    env.init()
    action = cancel_action(env)
    turns = iter([
        AssistantMessage(role="assistant", tool_calls=[ToolCall(id="cancel", name=action.name,
                                                               arguments=action.arguments)], cost=0),
        AssistantMessage(role="assistant", content="Cancelled. Goodbye.", cost=0),
    ])
    from tau2.utils.utils import get_now
    # Native runner sorts by timestamp; mock outputs must be stamped when emitted.
    monkeypatch.setattr(agent_module, "generate", lambda **kwargs:
                        next(turns).model_copy(update={"timestamp": get_now()}))
    monkeypatch.setattr(user_module, "generate", mock_user)
    task = make_task([action])
    simulation = env.run(task, {"agent": agent, "llm_agent": "mock", "seed": 7, "max_steps": 10},
                         {"llm_user": "mock"})
    assert isinstance(simulation, SimulationRun)
    assert simulation.task_id == task.id
    assert simulation.termination_reason == TerminationReason.USER_STOP
    assert simulation.reward_info.reward == 1
    assert any(m.role == "tool" for m in simulation.messages)
    assert env.reward(task, simulation).reward == 1


@pytest.mark.parametrize("invalid", ["none", "nl"])
def test_unsafe_reward_contract_rejected(invalid):
    task = make_task([])
    if invalid == "none":
        task.evaluation_criteria.actions = None
    else:
        task.evaluation_criteria.reward_basis = [RewardType.DB, RewardType.NL_ASSERTION]
    with pytest.raises(ValueError):
        RetailSynthEnv().reward(task, replay(task, []))


def test_holdout_identity_sources():
    spec = importlib.util.spec_from_file_location("build_holdout", REPO_ROOT / "scripts/build_holdout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = {"users": {"u": {"name": {"first_name": "Alice", "last_name": "Lee"},
                          "email": "alice@example.com", "address": {"zip": "01234"}}},
          "orders": {"#1": {"user_id": "u"}}}
    tasks = []
    for i, (args, known) in enumerate([
        ({"user_id": "u"}, ""), ({"order_id": "#1"}, ""),
        ({}, "Email: ALICE@example.com."), ({}, "Alice Lee in zip code 01234"),
        ({"first_name": "Alice", "last_name": "Lee", "zip": "01234"}, ""),
        ({"order_id": "#invalid"}, "You are u."),
    ]):
        tasks.append({"id": str(i), "evaluation_criteria": {"actions": [
            {"name": "read", "arguments": args}, {"name": "write", "arguments": {}}]},
            "user_scenario": {"instructions": {"known_info": known}}})
    users, signatures, evidence = module.extract_holdout(tasks, [t["id"] for t in tasks], db, {"write"})
    assert users == ["u"]
    assert len(evidence) == len(signatures) == 6
    assert all(s["write_actions"] == ["write"] for s in signatures)
    tasks[0]["evaluation_criteria"]["actions"] = []
    with pytest.raises(ValueError, match="unresolved"):
        module.extract_holdout(tasks, ["0"], db, {"write"})
