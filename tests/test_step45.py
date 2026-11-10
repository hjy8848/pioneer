"""[INDEPENDENT] Step 4.5: entity reuse, instance-specific rules, preflight statistics."""
import json

import pytest
from helpers.verifier_cases import candidate
from pioneer.env import RetailSynthEnv
from pioneer.explorer.audit import audit_world_model
from pioneer.explorer.agent import ExplorerAgent
from pioneer.initializer.agent import InitializerAgent
from pioneer.initializer.diversity import EntityLedger, candidate_pool, derive_entity_constraints
from pioneer.initializer.requirements import WAIT_FOR_COMPLETION
from pioneer.llm import MockLLM
from pioneer.paths import REPO_ROOT
from pioneer.schemas import ExpectedOutcome, Intent, Scenario
from pioneer.stats.ledger import summarize_ledger

WORLD = json.loads((REPO_ROOT / "data/knowledge/retail_world_model.json").read_text(encoding="utf-8"))


@pytest.fixture
def env():
    e = RetailSynthEnv()
    e.init()
    return e


def test_opaque_identifiers_are_derived_from_the_schema(env):
    expected = {v["item_id"] for v in env.query_data("variants")} | \
               {pid for u in env.query_data("users") for pid in u["payment_methods"]}
    assert env.opaque_identifiers() == expected
    assert env.opaque_id_paths() == ["products.*.variants", "users.*.payment_methods"]


def test_probe_rejects_checks_that_hard_code_an_entity_id(tmp_path, env):
    holdout = tmp_path / "h.json"
    holdout.write_text("[]")
    agent = ExplorerAgent(MockLLM([]), holdout)
    card = next(iter(next(iter(env.query_data("users")))["payment_methods"]))
    check = {"compare": [{"ref": "args.payment_method_id"}, "eq", card]}
    with pytest.raises(ValueError, match="hard-codes entity identifiers"):
        agent.probe("r", "modify_pending_order_payment", "t", check, {}, {})


def test_audit_moves_instance_specific_rules_and_is_idempotent(env):
    card = next(iter(env.query_data("users")[0]["payment_methods"]))
    world = dict(
        rules=[dict(rule_id="good", classification="confirmed_by_tool", text="g", check={"compare": [{"ref": "args.x"}, "in", ["a", "b"]]}),
               dict(rule_id="bad", classification="confirmed_by_tool", text="b", check={"compare": [{"ref": "args.x"}, "eq", card]})],
        discarded_rules=[], conversation_rules=[],
        actions={"t": dict(requires=[dict(text="b", evidence=["bad"]), dict(text="g", evidence=["good"])],
                           failure_modes=[], changes=[])})
    audited, report = audit_world_model(world, env.opaque_identifiers(), "policy")
    assert report["removed"] == ["bad"]
    assert [r["rule_id"] for r in audited["rules"]] == ["good"]
    assert audited["discarded_rules"][0]["classification"] == "instance_specific"
    assert [c["evidence"] for c in audited["actions"]["t"]["requires"]] == [["good"]]
    assert audit_world_model(audited, env.opaque_identifiers(), "policy")[1]["removed"] == []


def test_entity_constraints_come_from_verified_rules():
    assert derive_entity_constraints(WORLD, "cancel_pending_order") == {"orders": [["status", "eq", "pending"]]}
    assert derive_entity_constraints(WORLD, "return_delivered_order_items") == {"orders": [["status", "eq", "delivered"]]}
    assert derive_entity_constraints(WORLD, "modify_user_address") == {"users": []}


def test_ledger_blocks_reused_pair_and_users_over_the_limit(env):
    task, meta, order, user = candidate()
    ledger = EntityLedger(max_tasks_per_user=1)
    users, entities = ledger.entities_of(task, meta, env)
    assert users == {user["user_id"]} and entities == {f"orders:{order['order_id']}"}
    assert ledger.violation("cancel_pending_order", users, entities) is None
    ledger.register("cancel_pending_order", users, entities)
    assert "already used" in ledger.violation("cancel_pending_order", users, entities)
    assert "already appear" in ledger.violation("other_action", users, set())


def test_pool_is_seeded_excludes_holdout_and_used_entities(env):
    task, meta, order, user = candidate()
    ledger = EntityLedger(max_tasks_per_user=2)
    ledger.register("cancel_pending_order", *ledger.entities_of(task, meta, env))
    args = dict(world=WORLD, atomic_action="cancel_pending_order", ledger=ledger, size=20)
    first = candidate_pool(env, seed="s", holdout={"nobody"}, **args)
    assert first == candidate_pool(env, seed="s", holdout={"nobody"}, **args)
    assert first[1] != candidate_pool(env, seed="t", holdout={"nobody"}, **args)[1]
    assert order["order_id"] not in {e["order_id"] for e in first[1]}
    blocked = candidate_pool(env, seed="s", holdout={e["user_id"] for e in first[1]}, **args)
    assert not {e["user_id"] for e in first[1]} & {e["user_id"] for e in blocked[1]}


def test_two_first_pass_definitions_differ_when_preflight_self_repaired():
    ok = dict(passed=True, stage="accepted", failed_checks=[], failure_codes=[])
    bad = dict(passed=False, stage="preflight", failed_checks=["E4"], failure_codes=["ARG_NOT_DERIVABLE"])
    row = lambda cid, subs: dict(candidate_id=cid, atomic_action="a", round=0, passed=True,
                                 preflight_submissions=subs, generation_usage={}, validation_usage={}, llm_usage={"cost_usd": 1})
    old = dict(candidate_id="old", atomic_action="a", round=0, passed=True, llm_usage={"cost_usd": 1})
    summary = summarize_ledger([row("x", [ok]), row("y", [bad, ok]), old])
    assert summary["first_pass"]["outer"] == dict(count=3, of=3)
    assert summary["first_pass"]["with_preflight"]["count"] == 1
    assert summary["preflight"]["tasks_without_log"] == 1  # not guessed
    assert summary["preflight"]["failure_codes"] == {"ARG_NOT_DERIVABLE": 1}


def test_initializer_logs_every_submission(tmp_path):
    holdout = tmp_path / "h.json"
    holdout.write_text("[]")
    task, meta, *_ = candidate()
    call = lambda n, a, i: dict(id=str(i), type="function", function=dict(name=n, arguments=json.dumps(a)))
    submit = lambda t, m: dict(role="assistant", tool_calls=[call("submit_candidate", dict(task=t.model_dump(mode="json"), meta=m.model_dump(mode="json")), 9)])
    bad = task.model_copy(deep=True)  # lacks the wait-for-completion sentence
    good = task.model_copy(deep=True)
    good.user_scenario.instructions.task_instructions += " " + WAIT_FOR_COMPLETION
    llm = MockLLM([dict(role="assistant", tool_calls=[call("query_data", dict(collection="orders", limit=1), 0)]),
                   submit(bad, meta), submit(good, meta)])
    agent = InitializerAgent(llm, WORLD, holdout)
    agent.run(Scenario(scenario_id="s", intents=[Intent(atomic_action="cancel_pending_order")],
                       expected_outcome=ExpectedOutcome.COMPLETE), "cid")
    assert [s["stage"] for s in agent.submissions] == ["contract", "accepted"]
