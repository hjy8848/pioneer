"""[INDEPENDENT] Every FailureCode has a hand-constructed rejection case."""
import json
from types import SimpleNamespace

import pytest
from tau2.data_model.tasks import Action
from pioneer.env import RetailSynthEnv
from pioneer.paths import REPO_ROOT
from pioneer.schemas import FailureCode, ExpectedOutcome, Stressor
from pioneer.verifier import EvidenceEngine, VerifierAgent, task_signatures
from pioneer.llm import MockLLM
from helpers.verifier_cases import candidate, inform_candidate, reference_episode, read


@pytest.fixture(scope="module")
def knowledge():
    return json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))


def engine(knowledge, **kwargs):
    return EvidenceEngine(knowledge, episode_runner=reference_episode, **kwargs)


def test_valid_write_and_inform_use_mandatory_e8(knowledge):
    implementations = []
    def episode(task, implementation, seed):
        implementations.append(implementation)
        return reference_episode(task)
    for factory, implementation in ((candidate, "llm_agent_gt"), (inform_candidate, "llm_agent")):
        task, meta, *_ = factory()
        report = EvidenceEngine(knowledge, episode_runner=episode).evaluate(task, meta)
        assert report.passed, report.model_dump()
        assert implementations[-2:] == [implementation, implementation]
        assert len(report.checks) == 9


@pytest.mark.parametrize("code", list(FailureCode))
def test_failure_code_counterexamples(code, knowledge, monkeypatch):
    task, meta, order, user = candidate()
    options = {}
    if code == FailureCode.TOOL_ERROR:
        task.evaluation_criteria.actions[0].name = "unsupported_test_tool"
    elif code == FailureCode.POLICY_VIOLATION:
        task, meta, order, user = candidate(status="delivered")
    elif code == FailureCode.NO_STATE_CHANGE:
        task.evaluation_criteria.actions = [Action(action_id="same", name="modify_user_address", arguments=dict(user_id=user["user_id"], **user["address"]))]
        meta.argument_bindings = {"0.user_id": read(0), **{f"0.{k}": read(1, f"address.{k}") for k in user["address"]}}
    elif code == FailureCode.UNEXPECTED_DIFF:
        original = RetailSynthEnv.execute
        fresh = RetailSynthEnv()
        fresh.init()
        other = fresh.query_data("users", [("user_id", "ne", user["user_id"])], limit=1)[0]["user_id"]
        def inject(self, name, arguments=None, requestor="assistant"):
            result = original(self, name, arguments, requestor)
            if name == "cancel_pending_order":
                self.env.tools.db.users[other].address.city = "Unexpected test mutation"
            return result
        monkeypatch.setattr(RetailSynthEnv, "execute", inject)
    elif code == FailureCode.ARG_NOT_DERIVABLE:
        meta.argument_bindings.pop("0.reason")
    elif code == FailureCode.ID_LEAK:
        task.user_scenario.instructions.known_info += " Item ID: " + order["items"][0]["item_id"]
    elif code == FailureCode.REFUSAL_NOT_FORBIDDEN:
        meta.expected_outcome = ExpectedOutcome.REFUSE
        meta.refusal_requests = [dict(tool=task.evaluation_criteria.actions[0].name, arguments=task.evaluation_criteria.actions[0].arguments)]
        task.evaluation_criteria.actions = []
        meta.argument_bindings = {}
    elif code == FailureCode.AMBIGUITY_INVALID:
        meta.stressors = [Stressor.UNDERSPECIFIED]
        meta.ambiguity = dict(collection="orders", initial_where=[("order_id", "eq", order["order_id"])], clarified_where=[])
    elif code == FailureCode.REWARD_MISMATCH:
        monkeypatch.setattr(RetailSynthEnv, "reward", lambda *a: SimpleNamespace(reward=0))
    elif code == FailureCode.UNREACHABLE_E2E:
        bad = reference_episode(task, omit_actions=True)
        from tau2.data_model.simulation import RewardInfo
        bad.reward_info = RewardInfo(reward=1)  # forged flag cannot replace independent scoring
        runner = lambda *a: bad
        report = EvidenceEngine(knowledge, episode_runner=runner).evaluate(task, meta)
        assert code in report.failure_codes and not report.passed
        return
    elif code == FailureCode.DUPLICATE:
        options["seen_structure"] = [task_signatures(task, meta)[0]]
    elif code == FailureCode.HOLDOUT_LEAK:
        options["holdout"] = [user["user_id"]]
    report = engine(knowledge, **options).evaluate(task, meta)
    assert not report.passed
    assert code in report.failure_codes, report.model_dump()


def test_identity_must_be_unique(knowledge):
    task, meta, _, user = candidate()
    env = RetailSynthEnv()
    env.init()
    other = env.query_data("users", [("user_id", "ne", user["user_id"])], limit=1)[0]
    task.user_scenario.instructions.known_info += " Alternative email: " + other["email"]
    report = engine(knowledge).evaluate(task, meta)
    assert FailureCode.ARG_NOT_DERIVABLE in report.failure_codes


def test_other_users_order_is_not_legal_despite_tool_acceptance(knowledge):
    task, meta, order, user = candidate()
    env = RetailSynthEnv()
    env.init()
    other = env.query_data("orders", [("status", "eq", "pending"), ("user_id", "ne", user["user_id"])], limit=1)[0]
    task.user_scenario.instructions.known_info = task.user_scenario.instructions.known_info.replace(order["order_id"], other["order_id"])
    task.evaluation_criteria.actions[0].arguments["order_id"] = other["order_id"]
    meta.read_steps[2]["arguments"]["order_id"]["value"] = other["order_id"]
    report = engine(knowledge).evaluate(task, meta)
    assert next(c for c in report.checks if c.name == "E1").passed
    assert FailureCode.POLICY_VIOLATION in report.failure_codes


def test_policy_only_rule_rejects_an_executable_write(knowledge):
    from tau2.data_model.tasks import InitialState, InitializationData
    from helpers.verifier_cases import text
    task, meta, order, user = candidate()
    task.initial_state = InitialState(initialization_data=InitializationData(
        agent_data={"orders": {order["order_id"]: dict(order, status="pending (item modified)")}}))
    args = dict(order_id=order["order_id"], **dict(user["address"], address1="123 Test Avenue"))
    task.evaluation_criteria.actions = [Action(action_id="address", name="modify_pending_order_address", arguments=args)]
    task.user_scenario.instructions.reason_for_call = "Change shipping address to 123 Test Avenue."
    meta.argument_bindings = {"0.order_id": read(2, "order_id"), **{
        f"0.{k}": (text("123 Test Avenue") if k == "address1" else read(1, f"address.{k}")) for k in user["address"]}}
    report = engine(knowledge).evaluate(task, meta)
    assert next(c for c in report.checks if c.name == "E1").passed
    assert FailureCode.POLICY_VIOLATION in report.failure_codes


def test_partial_refusal_is_checked_after_reference_prefix(knowledge):
    task, meta, *_ = candidate()
    action = task.evaluation_criteria.actions[0]
    meta.expected_outcome = ExpectedOutcome.PARTIAL
    meta.refusal_requests = [dict(tool=action.name, arguments=action.arguments, after_actions=1)]
    report = engine(knowledge).evaluate(task, meta)
    assert report.passed, report.model_dump()


def test_official_signature_overlap_is_rejected(knowledge):
    task, meta, _, user = candidate()
    report = engine(knowledge, official_signatures=[dict(user_id=user["user_id"], write_actions=["cancel_pending_order"])]).evaluate(task, meta)
    assert FailureCode.DUPLICATE in report.failure_codes


def test_instance_signature_ignores_stressor_labels_and_action_ids():
    task, meta, *_ = candidate()
    original = task_signatures(task, meta)
    meta.stressors = [Stressor.REASONING]
    task.evaluation_criteria.actions[0].action_id = "renamed"
    changed = task_signatures(task, meta)
    assert original[0] != changed[0]
    assert original[1] == changed[1]


def test_verifier_agent_cannot_override_failed_evidence(knowledge):
    task, meta, *_ = candidate()
    meta.argument_bindings = {}
    def call(name, args, idx):
        return dict(id=str(idx), type="function", function=dict(name=name, arguments=json.dumps(args)))
    llm = MockLLM([
        dict(role="assistant", tool_calls=[call("check_evidence", {"name": f"E{i}"}, i) for i in range(1, 10)]),
        dict(role="assistant", tool_calls=[call("submit_report", {"diagnosis": [{"check": "E4", "cause": "The argument binding is absent."}], "passed": True}, 10)]),
    ])
    report = VerifierAgent(llm, engine(knowledge)).run(task, meta)
    assert not report.passed and FailureCode.ARG_NOT_DERIVABLE in report.failure_codes


def test_native_bridge_restores_provider_and_uses_mock(knowledge):
    from pioneer.llm.native_bridge import native_generation
    from tau2.utils.utils import get_now
    import tau2.agent.llm_agent as module
    task, _, *_ = inform_candidate()
    original = module.generate
    llm = MockLLM([dict(role="assistant", content="Hello")])
    with native_generation(llm):
        from tau2.data_model.message import UserMessage
        answer = module.generate(model="mock", messages=[UserMessage(role="user", content="Hi")])
        assert answer.content == "Hello"
    assert module.generate is original and llm.usage["calls"] == 1
