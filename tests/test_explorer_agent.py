"""[INDEPENDENT] Native tool-use loop, evidence admission and sandbox boundaries."""
import json

import pytest
from pioneer.env import RetailSynthEnv
from pioneer.explorer.agent import ExplorerAgent
from helpers.legacy_probes import make_case
from helpers.legacy_rules import rule_catalog
from pioneer.llm import MockLLM


def calls(specs):
    return {"role": "assistant", "tool_calls": [dict(id=f"call_{i}", type="function",
        function=dict(name=name, arguments=json.dumps(args))) for i, (name, args) in enumerate(specs)]}


@pytest.fixture
def agent(tmp_path):
    path = tmp_path / "holdout.json"
    path.write_text("[]", encoding="utf-8")
    return ExplorerAgent(MockLLM([]), path)


def specimens(agent):
    env = RetailSynthEnv()
    env.init()
    user = env.query_data("users", limit=1)[0]
    order = env.query_data("orders", limit=1)[0]
    product = env.query_data("products", limit=1)[0]
    variant = env.query_data("variants", limit=1)[0]
    basic = dict(
        calculate={"expression": "1 + 1"}, transfer_to_human_agents={"summary": "outside scope"},
        find_user_id_by_email={"email": user["email"]},
        find_user_id_by_name_zip=dict(user["name"], zip=user["address"]["zip"]),
        get_order_details={"order_id": order["order_id"]},
        get_product_details={"product_id": product["product_id"]},
        get_item_details={"item_id": variant["item_id"]},
        get_user_details={"user_id": user["user_id"]}, list_all_product_types={},
        modify_user_address=dict(user_id=user["user_id"], **dict(user["address"], address1="New Address")))
    result = []
    for tool in sorted(agent.exposed):
        if tool in basic:
            result.append(("sandbox_execute", dict(tool=tool, arguments=basic[tool])))
            continue
        rule = next(r for r in rule_catalog() if r["tools"] == [tool] and r["level"] == "state")
        sandbox, arguments = make_case(rule, False, set())
        oid = arguments["order_id"]
        order = sandbox.query_data("orders", [("order_id", "eq", oid)])[0]
        user = sandbox.query_data("users", [("user_id", "eq", order["user_id"])])[0]
        result.append(("sandbox_execute", dict(tool=tool, arguments=arguments, patches=[
            dict(collection="orders", id=oid, changes={k:v for k,v in order.items() if k not in {"order_id", "user_id"}}),
            dict(collection="users", id=user["user_id"], changes={k:v for k,v in user.items() if k != "user_id"}),
        ])))
    return result


def test_agent_loop_with_mock_llm(agent, tmp_path):
    observations = specimens(agent)
    # Explicit data and probe choices come from the scripted test agent.
    cancel = next(s[1] for s in observations if s[1]["tool"] == "cancel_pending_order")
    probe = dict(rule_id="cancel_pending", tool="cancel_pending_order", text="Cancellation needs pending status",
        check={"collection": "orders", "where": [["order_id", "eq", {"ref": "args.order_id"}], ["status", "eq", "pending"]]},
        positive={"arguments": cancel["arguments"]}, negative={"arguments": cancel["arguments"],
            "patches": [dict(collection="orders", id=cancel["arguments"]["order_id"], changes={"status": "delivered"})]})
    summaries = {spec[1]["tool"]: dict(requires=[], failure_modes=[], changes=[
        {"text": "Observed native tool response", "evidence": [f"observation_{i}"]}]) for i,spec in enumerate(observations)}
    summaries["cancel_pending_order"]["requires"] = [{"text": "Needs pending status", "evidence": ["cancel_pending"]}]
    writes = [n for n,t in agent.methods.items() if t["tool_type"] == "write"]
    submit = dict(actions=summaries, atomic_actions=writes+["inform:order_status", "transfer_to_human_agents"], conversation_rules=[])
    agent.llm = MockLLM([
        calls([("read_source", {"path": p}) for p in ("tools.py", "data_model.py", "policy.md")]+[
            ("get_metadata", {}), ("query_data", {"collection": "users", "limit": 1})]),
        calls(observations+[("probe_condition", probe)]), calls([("submit_world_model", submit)])])
    output = agent.run(tmp_path / "world.json", max_turns=3)
    assert output["rule_counts"] == {"confirmed_by_tool": 1}
    assert len(output["observations"]) == 16
    assert output["llm_usage"]["calls"] == 3
    assert all(o["execution"]["ok"] for o in output["observations"].values())
    assert output["actions"]["cancel_pending_order"]["requires"][0]["verified_checks"] == [probe["check"]]
    assert "verified_outputs" in output["actions"]["calculate"]["changes"][0]


def test_source_and_identity_boundaries(agent):
    with pytest.raises(ValueError, match="allowlisted"):
        agent.dispatch("read_source", {"path": "../private"})
    with pytest.raises(ValueError, match="identity"):
        env = RetailSynthEnv()
        env.init()
        order = env.query_data("orders", limit=1)[0]
        agent.case("get_order_details", {"order_id": order["order_id"]}, patches=[
            dict(collection="orders", id=order["order_id"], changes={"user_id": "other"})])
    agent.holdout = {order["user_id"]}
    with pytest.raises(ValueError, match="holdout"):
        agent.observe("get_order_details", {"order_id": order["order_id"]})
    user = env.query_data("users", [("user_id", "eq", order["user_id"])])[0]
    with pytest.raises(ValueError, match="holdout"):
        agent.observe("find_user_id_by_email", {"email": user["email"]})
    assert all(u["user_id"] not in agent.holdout for u in agent.dispatch("query_data", {"collection": "users"}))


def test_unconfirmed_claim_cannot_be_submitted(agent):
    agent.visited = {"read_source:tools.py", "read_source:data_model.py", "read_source:policy.md", "get_metadata", "query_data"}
    actions = {n: dict(requires=[{"text": "LLM says so", "evidence": ["fabricated"]}], changes=[], failure_modes=[]) for n in agent.exposed}
    with pytest.raises(ValueError, match="unconfirmed"):
        agent.submit(actions, [], [])


def test_agent_budget_does_not_emit_partial_world_model(agent, tmp_path):
    agent.llm = MockLLM([{"role": "assistant", "content": "looks good"}])
    with pytest.raises(RuntimeError, match="budget"):
        agent.run(tmp_path / "world.json", max_turns=1)
    assert not (tmp_path / "world.json").exists()
