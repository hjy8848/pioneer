"""[INDEPENDENT] Step 5: tracker, Evolver agent, depth>0 contract, condition evidence, picks."""
import json

import pytest
from helpers.verifier_cases import candidate, reference_episode
from pioneer import pipeline
from pioneer.evolver import CoverageTracker, EvolverAgent, parent_scenario, validate_scenario
from pioneer.initializer.agent import DEEPER, SYSTEM, InitializerAgent, build_system
from pioneer.initializer.contract import check_actions, scenario_key
from pioneer.initializer.requirements import WAIT_FOR_COMPLETION
from pioneer.llm import MockLLM
from pioneer.paths import REPO_ROOT
from pioneer.schemas import ExpectedOutcome, FailureCode, Intent, Scenario, Stressor
from pioneer.verifier import EvidenceEngine
from pioneer.verifier.grounding import resolve

WORLD = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))
ATOMS = WORLD["atomic_actions"]


def scenario(*actions, outcome=ExpectedOutcome.COMPLETE, depth=1, stressors=(), **extra):
    return Scenario(scenario_id="s", depth=depth, stressors=list(stressors), expected_outcome=outcome,
                    intents=[Intent(atomic_action=a, notes=extra.pop("notes", None)) for a in actions], **extra)


def row(task_id, depth=0, outcome="complete", stressors=(), actions=("cancel_pending_order",), refusals=(),
        parent=None, passed=True):
    return dict(task=dict(id=task_id, evaluation_criteria=dict(actions=[dict(name=a) for a in actions])),
                meta=dict(depth=depth, expected_outcome=outcome, stressors=list(stressors), scenario_id=task_id,
                          num_write_actions=len(actions), parent_id=parent, instance_signature=task_id,
                          refusal_requests=[dict(tool=t, arguments={}) for t in refusals]), passed=passed)


# -- tracker --------------------------------------------------------------------------------------
def test_tracker_counts_and_gaps():
    t = CoverageTracker(WORLD).add_all([
        row("a"), row("b", depth=1, outcome="refuse", stressors=["policy_pushback"], actions=(),
                      refusals=("cancel_pending_order",), parent="a"),
        row("c", depth=1, stressors=["chain"], actions=("cancel_pending_order", "modify_user_address"), parent="a")])
    s = t.summary()
    assert s["covered"]["outcome"] == "2/5" and s["counts"]["depth"] == {"0": 1, "1": 2}
    assert s["counts"]["tool_pair"] == {"cancel_pending_order+modify_user_address": 1}
    assert s["counts"]["write_tool"]["cancel_pending_order"] == 3  # a refused request still touches its tool
    assert "refuse" not in t.gaps()["outcome"] and "partial" in t.gaps()["outcome"]
    assert t.gaps()["depth"] == ["2", "3", "4"]  # depth 0 is the start, not a gap
    t.add(row("a"))  # same task id twice
    assert t.summary()["tasks"] == 3


def test_plan_is_deterministic_forces_pushback_and_fills_the_shallowest_short_layer():
    pool = [row("a"), row("b"), row("deep", depth=1, stressors=["chain"], parent="a")]
    t = CoverageTracker(WORLD).add_all(pool)
    plan = t.plan(4, seed=7)
    assert plan == t.plan(4, seed=7)
    assert [p["stressor"] for p in plan[:2]] == ["policy_pushback", "policy_pushback"]  # refuse and partial both missing
    assert {p["parent_id"] for p in plan} == {"a", "b"}  # depth 1 is short of its target: parents at depth 0
    assert len({p["stressor"] for p in plan}) >= 3


def test_plan_does_not_pile_a_generation_onto_the_one_task_of_a_new_layer():
    # the batch that collapsed: 28 depth-0 tasks and a single depth-1 task, 20 slots
    pool = [row(f"d0_{i}") for i in range(28)] + [row("only_d1", depth=1, parent="d0_0")]
    plan = CoverageTracker(WORLD).add_all(pool).plan(20, seed=0)
    parents = [p["parent_id"] for p in plan]
    assert len(plan) == 20 and "only_d1" not in parents and len(set(parents)) == 20


def test_pool_keeps_a_later_task_that_reuses_an_id_under_a_run_tagged_id(tmp_path, capsys):
    from pioneer.evolver import load_pool
    files = []
    for run, signature in (("run_a", "sig_a"), ("run_b", "sig_b")):
        f = tmp_path / run / "shards" / "evo_g1_s0" / "candidates.jsonl"
        f.parent.mkdir(parents=True)
        r = row("evo:g1:s0", depth=1, parent="d0")
        r["meta"]["instance_signature"] = signature
        f.write_text(json.dumps(r) + "\n", encoding="utf-8")
        files.append(f)
    pool = load_pool(files)
    assert [r["task"]["id"] for r in pool] == ["evo:g1:s0", "evo:g1:s0@run_b"]
    assert "already used" in capsys.readouterr().out


def test_plan_caps_children_per_parent_and_moves_to_the_next_layer():
    pool = [row("p"), row("c1", depth=1, parent="p")]
    t = CoverageTracker(WORLD, depth_targets=[1, 2, 5, 5, 5], max_children_per_parent=2).add_all(pool)
    plan = t.plan(10, seed=0)
    # depth 1 needs one more (target 2): from p; then depth 2 from c1 (capped at 2); then any open parent
    assert [p["parent_id"] for p in plan][:1] == ["p"]
    assert sorted(p["parent_id"] for p in plan) == ["c1", "c1", "p", "p"]  # everyone capped at 2: only 4 slots


def test_plan_never_evolves_past_the_maximum_depth_and_spreads_children():
    t = CoverageTracker(WORLD, max_depth=1).add_all([row("a", depth=1, parent="x"), row("b"), row("c")])
    assert sorted(p["parent_id"] for p in t.plan(2, seed=0)) == ["b", "c"]  # "a" is already at the maximum depth
    assert CoverageTracker(WORLD, max_depth=0).add_all([row("a")]).plan(3, seed=0) == []


# -- scenario shape checks ----------------------------------------------------------------------
PARENT = scenario("cancel_pending_order", depth=0, stressors=())
CASES = [
    (Stressor.CHAIN, scenario("cancel_pending_order", "modify_user_address"), True),
    (Stressor.CHAIN, scenario("cancel_pending_order", "cancel_pending_order"), False),  # same kind is multi_entity
    (Stressor.MULTI_ENTITY, scenario("cancel_pending_order", "cancel_pending_order"), True),
    (Stressor.MULTI_ENTITY, scenario("cancel_pending_order", "modify_user_address"), False),
    (Stressor.POLICY_PUSHBACK, scenario("cancel_pending_order", outcome=ExpectedOutcome.REFUSE), True),
    (Stressor.POLICY_PUSHBACK, scenario("cancel_pending_order", "modify_user_address"), False),
    (Stressor.CONDITIONAL, scenario("cancel_pending_order", "modify_user_address", conditions=dict(
        collection="orders", where=[], holds=True, if_true="cancel_pending_order", if_false="modify_user_address")), True),
    (Stressor.CONDITIONAL, scenario("cancel_pending_order", "modify_user_address"), False),
    (Stressor.UNDERSPECIFIED, scenario("cancel_pending_order", ambiguity=dict(
        collection="orders", initial_where=[], clarified_where=[])), True),
    (Stressor.UNDERSPECIFIED, scenario("cancel_pending_order", outcome=ExpectedOutcome.PARTIAL), False),
    (Stressor.REASONING, scenario("cancel_pending_order", notes="the cheapest of the user's orders"), True),
    (Stressor.REASONING, scenario("cancel_pending_order", "modify_user_address"), False),
]


@pytest.mark.parametrize("stressor,child,ok", CASES)
def test_scenario_shape_per_stressor(stressor, child, ok):
    child = child.model_copy(update=dict(rationale="why", depth=1))
    if ok:
        validate_scenario(PARENT, stressor, child, ATOMS, 4)
    else:
        with pytest.raises(ValueError):
            validate_scenario(PARENT, stressor, child, ATOMS, 4)


@pytest.mark.parametrize("stressor,child,problem", [
    # the batch's dead end: partial with a single write intent has no non-empty strict subset to perform
    (Stressor.POLICY_PUSHBACK, scenario("inform: user details", "cancel_pending_order", outcome=ExpectedOutcome.PARTIAL),
     "at least two non-inform intents"),
    (Stressor.POLICY_PUSHBACK, scenario("inform: user details", outcome=ExpectedOutcome.REFUSE),
     "refuse needs a non-inform intent"),
    (Stressor.CONDITIONAL, scenario("cancel_pending_order", "inform: order details", conditions=dict(
        collection="orders", where=[], holds=True, if_true="cancel_pending_order", if_false="inform: order details")),
     "must be a write action"),
])
def test_scenario_that_can_never_be_grounded_is_returned_to_the_evolver(stressor, child, problem):
    child = child.model_copy(update=dict(rationale="why", depth=1))
    with pytest.raises(ValueError, match=problem):
        validate_scenario(PARENT, stressor, child, ATOMS, 4)
    ok = scenario("cancel_pending_order", "modify_user_address", outcome=ExpectedOutcome.PARTIAL)
    validate_scenario(PARENT, Stressor.POLICY_PUSHBACK, ok.model_copy(update=dict(rationale="why", depth=1)), ATOMS, 4)


def test_scenario_must_differ_have_rationale_known_actions_and_depth_room():
    chained = scenario("cancel_pending_order", "modify_user_address", rationale="r")
    with pytest.raises(ValueError, match="equals its parent"):
        validate_scenario(PARENT, Stressor.CHAIN, scenario("cancel_pending_order", rationale="r"), ATOMS, 4)
    with pytest.raises(ValueError, match="rationale"):
        validate_scenario(PARENT, Stressor.CHAIN, scenario("cancel_pending_order", "modify_user_address"), ATOMS, 4)
    with pytest.raises(ValueError, match="unknown atomic actions"):
        validate_scenario(PARENT, Stressor.CHAIN, scenario("cancel_pending_order", "teleport", rationale="r"), ATOMS, 4)
    with pytest.raises(ValueError, match="exceeds"):
        validate_scenario(PARENT.model_copy(update=dict(depth=4)), Stressor.CHAIN, chained, ATOMS, 4)


# -- Evolver agent --------------------------------------------------------------------------------
def call(name, args, i=0):
    return dict(id=str(i), type="function", function=dict(name=name, arguments=json.dumps(args)))


def test_evolver_overrides_bookkeeping_and_retries_after_a_rejected_draft(tmp_path):
    holdout = tmp_path / "h.json"
    holdout.write_text("[]")
    good = dict(intents=[dict(atomic_action="cancel_pending_order"), dict(atomic_action="modify_user_address")],
                expected_outcome="complete", rationale="adds a second change", depth=99, parent_id="liar", stressors=[])
    bad = dict(good, intents=[dict(atomic_action="cancel_pending_order")])
    llm = MockLLM([dict(role="assistant", tool_calls=[call("submit_scenario", dict(scenario=bad), 1)]),
                   dict(role="assistant", tool_calls=[call("submit_scenario", dict(scenario=good), 2)])])
    parent_row = row("depth0:cancel_pending_order:0")
    drafted = EvolverAgent(llm, WORLD, holdout).run(parent_row, PARENT, Stressor.CHAIN, "evo:g1:s0", gaps={})
    assert (drafted.depth, drafted.parent_id, drafted.scenario_id) == (1, "depth0:cancel_pending_order:0", "evo:g1:s0")
    assert drafted.stressors == [Stressor.CHAIN] and llm.usage["calls"] == 2
    payload = json.loads(llm.requests[0]["messages"][1]["content"])
    assert payload["stressor"] == "chain" and payload["parent"]["task"]["id"] == "depth0:cancel_pending_order:0"


def test_evolver_budget_end_is_an_error_not_a_scenario(tmp_path):
    holdout = tmp_path / "h.json"
    holdout.write_text("[]")
    with pytest.raises(RuntimeError, match="budget"):
        EvolverAgent(MockLLM([dict(role="assistant", content="hmm")]), WORLD, holdout).run(
            row("p"), PARENT, Stressor.CHAIN, "x", {}, max_turns=1)


def test_parent_scenario_recovers_depth_zero_atoms_and_requires_deeper_scenarios():
    assert parent_scenario(row("depth0:cancel_pending_order:2")).intents[0].atomic_action == "cancel_pending_order"
    assert parent_scenario(row("depth0:inform: order details:1", outcome="inform")).intents[0].atomic_action == "inform: order details"
    with pytest.raises(ValueError, match="carry its scenario"):
        parent_scenario(row("evo:g1:s0", depth=1))
    stored = row("evo:g1:s0", depth=1)
    stored["scenario"] = scenario("cancel_pending_order", "modify_user_address").model_dump(mode="json")
    assert len(parent_scenario(stored).intents) == 2


# -- depth > 0 contract -----------------------------------------------------------------------------
def test_depth_zero_contract_and_prompt_are_unchanged():
    depth0 = Scenario(scenario_id="s", intents=[Intent(atomic_action="cancel_pending_order")], expected_outcome=ExpectedOutcome.COMPLETE)
    check_actions(depth0, ["cancel_pending_order"])
    with pytest.raises(ValueError, match=r"reference tools must be \['cancel_pending_order'\]$"):
        check_actions(depth0, [])
    assert scenario_key(depth0) == "cancel_pending_order"
    assert build_system(dict(domain="d")) == SYSTEM.replace("{domain}", "d")
    assert build_system(dict(domain="d"), depth=2) == SYSTEM.replace("{domain}", "d") + DEEPER


@pytest.mark.parametrize("outcome,actions,ok", [
    ("complete", ["cancel_pending_order", "modify_user_address"], True),
    ("complete", ["modify_user_address", "cancel_pending_order"], False),  # order matters
    ("refuse", [], True), ("refuse", ["cancel_pending_order"], False),
    ("partial", ["cancel_pending_order"], True), ("partial", [], False),
    ("partial", ["cancel_pending_order", "modify_user_address"], False),  # not a strict subset
    ("transfer", ["transfer_to_human_agents"], True), ("transfer", [], False),
])
def test_deeper_contract_by_outcome(outcome, actions, ok):
    s = scenario("cancel_pending_order", "modify_user_address", outcome=ExpectedOutcome(outcome))
    if ok:
        check_actions(s, actions)
    else:
        with pytest.raises(ValueError):
            check_actions(s, actions)


def test_conditional_scenarios_accept_only_one_branch():
    s = scenario("cancel_pending_order", "modify_user_address", conditions=dict(
        collection="orders", where=[], holds=True, if_true="cancel_pending_order", if_false="modify_user_address"))
    check_actions(s, ["cancel_pending_order"])
    with pytest.raises(ValueError):
        check_actions(s, [])
    assert scenario_key(s) == "cancel_pending_order|modify_user_address@complete"


def test_initializer_deeper_run_sends_parent_and_logs_a_contract_failure(tmp_path):
    holdout = tmp_path / "h.json"
    holdout.write_text("[]")
    task, meta, *_ = candidate()  # carries a cancel action, which a refuse scenario must not
    task.user_scenario.instructions.task_instructions += " " + WAIT_FOR_COMPLETION
    refuse = scenario("cancel_pending_order", outcome=ExpectedOutcome.REFUSE, stressors=[Stressor.POLICY_PUSHBACK])
    llm = MockLLM([dict(role="assistant", tool_calls=[call("query_data", dict(collection="orders", limit=1), 0)]),
                   dict(role="assistant", tool_calls=[call("submit_candidate", dict(
                       task=task.model_dump(mode="json"), meta=meta.model_dump(mode="json")), 1)])])
    agent = InitializerAgent(llm, WORLD, holdout)
    with pytest.raises(RuntimeError, match="rework_queue"):
        agent.run(refuse, "cid", parent=dict(task={"id": "p"}, meta={}), max_turns=2)
    assert "Deeper scenarios" in llm.requests[0]["messages"][0]["content"]
    assert json.loads(llm.requests[0]["messages"][1]["content"])["parent"]["task"]["id"] == "p"
    assert agent.submissions[0]["stage"] == "contract" and "no reference actions" in agent.submissions[0]["error"]


# -- condition evidence (E6) and picks ----------------------------------------------------------
def engine():
    return EvidenceEngine(WORLD, episode_runner=reference_episode)


def conditional(holds_claimed, status_clause="pending", **override):
    task, meta, order, user = candidate()
    meta.stressors = [Stressor.CONDITIONAL]
    meta.condition = dict(collection="orders", where=[["order_id", "eq", order["order_id"]], ["status", "eq", status_clause]],
                          holds=holds_claimed, if_true="cancel_pending_order", if_false="modify_user_address", **override)
    return task, meta


def e6(report):
    return next(c for c in report.checks if c.name == "E6")


def test_condition_evidence_accepts_a_true_condition_with_its_branch_executed():
    report = engine().evaluate(*conditional(True))
    assert e6(report).passed and e6(report).details["condition"]["taken"] == "cancel_pending_order"


@pytest.mark.parametrize("task_meta,why", [
    (conditional(False), "claims the condition is false but the data says true"),
    (conditional(True, status_clause="delivered"), "claims true while the data says false"),
    (conditional(False, status_clause="delivered"), "false is right but then the else branch must run, not the cancel"),
])
def test_condition_evidence_rejects_claims_the_data_contradicts(task_meta, why):
    report = engine().evaluate(*task_meta)
    assert not e6(report).passed and FailureCode.CONDITION_INVALID in report.failure_codes, why


def test_conditional_stressor_without_a_condition_is_rejected():
    task, meta = conditional(True)
    meta.condition = None
    report = engine().evaluate(task, meta)
    assert FailureCode.CONDITION_INVALID in report.failure_codes and not e6(report).details["condition"].get("evaluated")


ROWS = [{"item_id": "a", "price": 5.0}, {"item_id": "b", "price": 2.0}, {"item_id": "c", "price": 9.0}]


def test_pick_selects_the_single_extreme_and_rejects_ties_and_non_numbers():
    pick = lambda kind, rows=ROWS, key="price": resolve(
        dict(source="read", step=0, path="", where=[], pick={kind: key}, field="item_id"), [rows], "")
    assert pick("min") == "b" and pick("max") == "c"
    with pytest.raises(ValueError, match="tied"):
        pick("min", ROWS + [{"item_id": "d", "price": 2.0}])
    with pytest.raises(ValueError, match="numeric"):
        pick("min", key="item_id")
    with pytest.raises(ValueError, match="min or max"):
        pick("sum")
    filtered = dict(source="read", step=0, path="", where=[["item_id", "ne", "b"]], pick=dict(min="price"), field="item_id")
    assert resolve(filtered, [ROWS], "b") == "a"  # operand 'b' must appear in the user text


def test_communicate_binding_is_matched_ignoring_case_like_the_native_evaluator():
    from helpers.verifier_cases import inform_candidate
    task, meta, order, _ = inform_candidate()
    task.evaluation_criteria.communicate_info = [order["status"].upper()]  # 'PENDING' vs the READ value 'pending'
    report = engine().evaluate(task, meta)
    assert report.passed, report.model_dump()  # E4 accepts the case difference and the native evaluator (E7) scores it 1
    assert task.evaluation_criteria.communicate_info != [order["status"]]


def test_client_retries_gateway_hiccups_but_not_other_errors(tmp_path, monkeypatch):
    import litellm
    from types import SimpleNamespace
    from pioneer.llm import LiteLLMClient

    class InternalServerError(Exception):
        pass

    class BadRequestError(Exception):
        pass

    reply = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, model_dump=lambda: {}),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", model_dump=lambda exclude_none=True: {"content": "ok"}))],
        _hidden_params={"response_cost": 0.0})
    script = [InternalServerError("Connection error"), InternalServerError("Connection error"), reply]
    sleeps = []

    def completion(**kwargs):
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr("time.sleep", sleeps.append)
    client = LiteLLMClient("m", 0, 0, tmp_path, backoff=30)
    assert client.chat([{"role": "user", "content": "hi"}])["content"] == "ok"
    assert sleeps == [30, 60] and client.usage["calls"] == 1  # two retries, one billed call

    script[:] = [BadRequestError("bad")]
    sleeps.clear()
    with pytest.raises(BadRequestError):
        client.chat([{"role": "user", "content": "other"}])
    assert sleeps == []  # not a transient error: no retry

    script[:] = [InternalServerError("down")] * 3
    with pytest.raises(InternalServerError):
        LiteLLMClient("m", 0, 0, tmp_path, retries=2, backoff=1).chat([{"role": "user", "content": "third"}])


def test_client_rotates_over_replicas_and_caches_independently_of_replica(tmp_path, monkeypatch):
    import litellm
    from types import SimpleNamespace
    from pioneer.llm import LiteLLMClient

    bases = []

    def completion(**kwargs):
        bases.append(kwargs["api_base"])
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, model_dump=lambda: {}),
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", model_dump=lambda exclude_none=True: {"content": "ok"}))],
            _hidden_params={"response_cost": 0.0})

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr("random.randrange", lambda n: 0)  # start at the first replica
    client = LiteLLMClient("m", 0, 0, tmp_path, api_base="http://a/v1, http://b/v1")
    for text in ("1", "2", "3"):
        client.chat([{"role": "user", "content": text}])
    assert bases == ["http://a/v1", "http://b/v1", "http://a/v1"]
    client.chat([{"role": "user", "content": "2"}])  # answered by b, found in the cache now
    assert len(bases) == 3 and client.usage["cache_hits"] == 1


def test_thinking_switch_is_sent_and_keys_the_cache(tmp_path, monkeypatch):
    import litellm
    from types import SimpleNamespace
    from pioneer.llm import LiteLLMClient

    sent = []

    def completion(**kwargs):
        sent.append(kwargs.get("extra_body"))
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, model_dump=lambda: {}),
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", model_dump=lambda exclude_none=True: {"content": "ok"}))],
            _hidden_params={"response_cost": 0.0})

    monkeypatch.setattr(litellm, "completion", completion)
    messages = [{"role": "user", "content": "hi"}]
    LiteLLMClient("m", 0, 0, tmp_path).chat(messages)
    LiteLLMClient("m", 0, 0, tmp_path, enable_thinking=False).chat(messages)  # a different request: not a cache hit
    per_role = LiteLLMClient("m", 0, 0, tmp_path, enable_thinking={"default": True, "verifier": False})
    per_role.chat(messages, role="verifier")     # same request as the all-off call above: cached
    per_role.chat(messages, role="initializer")  # falls back to default: thinking on, sent
    assert sent == [None] + [{"chat_template_kwargs": {"enable_thinking": v}} for v in (False, True)]
    assert per_role.usage["cache_hits"] == 1


# -- pipeline control flow ------------------------------------------------------------------------
class FakeClient:
    def __init__(self, **params):
        self.params, self.usage, self.records = params, dict(calls=0, cost_usd=0.0), [dict(cache_key="k")]


class Verdict:
    def __init__(self, passed):
        self.passed, self.failure_codes = passed, []

    def model_dump(self, mode=None):
        return {"passed": self.passed}


def stub_pipeline(monkeypatch, steps):
    """Initializer attempts follow ``steps``: raise, produce a task the Verifier rejects, or one it admits."""
    script, state = iter(steps), {}

    class Init:
        def __init__(self, *a):
            self.trace, self.submissions, self.last_proposal = [], [], None
            self.messages = [dict(role="system", content="initializer prompt")]

        def run(self, *a, **k):
            state["step"] = next(script)
            if state["step"] == "error":
                raise ValueError("preflight")
            task, meta, *_ = candidate()
            return task, meta

    monkeypatch.setattr(pipeline, "LiteLLMClient", FakeClient)
    monkeypatch.setattr(pipeline, "InitializerAgent", Init)
    monkeypatch.setattr(pipeline, "verify", lambda *a, **k: Verdict(state["step"] == "admit"))


def run_pipeline(rounds):
    written = []
    result = pipeline.run_rounds(scenario("cancel_pending_order", depth=0), "cid", knowledge={}, params=dict(seed=0),
        verifier_cfg=dict(e2e_trials=1), seed_instances=(), append=written.append, ledger_factory=lambda: None,
        rounds=rounds, atomic_action="k")
    return result, written


def test_run_rounds_reworks_until_the_task_is_admitted(monkeypatch):
    stub_pipeline(monkeypatch, ["error", "reject", "admit"])
    (record, previous, diagnosis), written = run_pipeline(rounds=5)
    assert record["round"] == 2 and record["passed"]
    assert [w["round"] for w in written] == [0, 1, 2] and [w["passed"] for w in written] == [False, False, True]
    assert "error" in written[0] and all(w["scenario"] and w["atomic_action"] == "k" for w in written)


def test_run_rounds_returns_the_unfixed_task_for_the_rework_queue(monkeypatch):
    stub_pipeline(monkeypatch, ["reject", "reject"])
    (record, previous, diagnosis), written = run_pipeline(rounds=2)
    assert record is None and len(written) == 2 and previous["task"]["id"] and diagnosis == {"passed": False}


def test_evolver_rewrites_a_scenario_the_rounds_could_not_fix(monkeypatch):
    stub_pipeline(monkeypatch, ["reject", "reject", "admit"])  # same proposal twice ends the rounds of version 1
    first = scenario("cancel_pending_order")
    second = scenario("cancel_pending_order", "modify_user_address")
    revisions, written = [], []

    def revise(old, diagnosis):
        revisions.append((old, diagnosis))
        return second

    record, previous, diagnosis, final = pipeline.run_with_rewrites(
        first, "cid", revise=revise, rewrites=2, knowledge={}, params=dict(seed=0), verifier_cfg=dict(e2e_trials=1),
        seed_instances=(), append=written.append, ledger_factory=lambda: None, rounds=5)
    assert record["passed"] and final is second and len(revisions) == 1
    assert revisions[0][0] is first and revisions[0][1]["failed_checks"] == []  # compacted report, no transcripts
    assert [w["round"] for w in written] == [0, 1, 2]  # round numbers continue across versions
    assert written[-1]["atomic_action"] == "cancel_pending_order|modify_user_address@complete"


def test_every_round_archives_its_full_conversation_and_call_keys(monkeypatch):
    stub_pipeline(monkeypatch, ["error", "admit"])
    kept = {}
    pipeline.run_rounds(scenario("cancel_pending_order", depth=0), "cid", knowledge={}, params=dict(seed=0),
        verifier_cfg=dict(e2e_trials=1), seed_instances=(), append=lambda r: None, ledger_factory=lambda: None,
        rounds=5, archive=lambda name, data: kept.update({name: data}))
    assert sorted(kept) == ["round0", "round1"]  # the failed round too
    assert kept["round0"]["initializer_messages"][0]["content"] == "initializer prompt"
    assert kept["round1"]["llm_calls"] == [dict(cache_key="k")]


def test_rewrites_stop_at_the_budget_and_a_failed_rewrite_queues(monkeypatch):
    stub_pipeline(monkeypatch, ["reject"] * 10)
    calls = []
    record, *_ = pipeline.run_with_rewrites(
        scenario("cancel_pending_order"), "cid", revise=lambda s, d: calls.append(1) or s, rewrites=1,
        knowledge={}, params=dict(seed=0), verifier_cfg=dict(e2e_trials=1), seed_instances=(),
        append=lambda r: None, ledger_factory=lambda: None, rounds=5)
    assert record is None and len(calls) == 1

    def broken(s, d):
        raise RuntimeError("Evolver did not submit")
    record, *_ = pipeline.run_with_rewrites(
        scenario("cancel_pending_order"), "cid", revise=broken, rewrites=2, knowledge={}, params=dict(seed=0),
        verifier_cfg=dict(e2e_trials=1), seed_instances=(), append=lambda r: None, ledger_factory=lambda: None, rounds=5)
    assert record is None


@pytest.mark.parametrize("step", ["reject", "error"])
def test_run_rounds_stops_when_a_round_would_replay_the_last(monkeypatch, step):
    stub_pipeline(monkeypatch, [step] * 5)
    (record, previous, diagnosis), written = run_pipeline(rounds=5)
    # round 1 sees round 0's output; round 2 would get the same proposal and diagnosis as round 1
    assert record is None and [w["round"] for w in written] == [0, 1]
