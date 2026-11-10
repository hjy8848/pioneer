"""[INDEPENDENT] Offline Explorer contract, policy gaps, and cache regression tests."""
import ast
import json
from types import SimpleNamespace

import pytest
from helpers.legacy_build import build_knowledge
from pioneer.explorer.source import extract_tools
from helpers.legacy_rules import rule_catalog
from helpers.legacy_probes import probe_rule
from pioneer.llm import LiteLLMClient, MockLLM
from pioneer.paths import TAU2_DIR, HOLDOUT_DIR


@pytest.fixture
def holdout_path(tmp_path):
    from pioneer.env import RetailSynthEnv
    env = RetailSynthEnv()
    env.init()
    users = [u["user_id"] for u in env.query_data("users", limit=10)]
    path = tmp_path / "users.json"
    path.write_text(json.dumps(users), encoding="utf-8")
    return path


def mock_response():
    tools = extract_tools(TAU2_DIR / "src/tau2/domains/retail/tools.py")
    rules = rule_catalog()
    for r in rules:
        r["evidence"] = "Retail agent policy"
    return dict(actions={name: dict(requires=["mock"], changes=[], failure_modes=[])
                         for name, t in tools.items() if t["tool_type"]}, rules=rules)


def test_ast_covers_every_valueerror_and_helpers():
    path = TAU2_DIR / "src/tau2/domains/retail/tools.py"
    tools = extract_tools(path)
    raises = [n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Raise)
              and isinstance(n.exc, ast.Call) and ast.unparse(n.exc.func) == "ValueError"]
    assert sum(len(t["raises"]) for t in tools.values()) == len(raises)
    assert len([t for t in tools.values() if t["tool_type"]]) == 16
    assert "_get_order" in tools["cancel_pending_order"]["calls"]
    assert any("order.status !=" in g["failure_when"][0]
               for g in tools["cancel_pending_order"]["raises"])


def test_build_knowledge_probes_and_isolation(tmp_path, holdout_path):
    mock = MockLLM([mock_response()])
    output = tmp_path / "knowledge.json"
    knowledge = build_knowledge(output, mock, holdout_path)
    assert json.loads(output.read_text()) == knowledge
    assert knowledge["rule_counts"] == {"enforced_by_tool": 16, "policy_only": 3, "conversation_only": 6}
    assert len([a for a in knowledge["atomic_actions"] if a["kind"] == "write"]) == 7
    assert knowledge["models"]["literals"]
    holdout = set(json.loads(holdout_path.read_text()))
    for r in knowledge["rules"]:
        if r["level"] == "state":
            positive, negative = r["probe"]["cases"]
            assert positive["execution"]["ok"]
            assert positive["check_passed"] and not negative["check_passed"]
            assert all(c["initial_state"]["user"]["user_id"] not in holdout for c in (positive, negative))
    assert mock.usage["calls"] == 1


@pytest.mark.parametrize("rule_id,label", [
    ("modify_pending_order_address:pending", "policy_only"),
    ("modify_pending_order_payment:pending", "policy_only"),
    ("exchange_delivered_order_items:different", "policy_only"),
    ("cancel_pending_order:pending", "enforced_by_tool"),
])
def test_required_policy_gaps(rule_id, label):
    rule = next(r for r in rule_catalog() if r["rule_id"] == rule_id)
    assert probe_rule(rule, set())["classification"] == label


def test_contradicted_rule_is_discarded(tmp_path, monkeypatch, holdout_path):
    import helpers.legacy_build as builder
    monkeypatch.setattr(builder, "probe_rule", lambda *a: {"classification": "contradicted", "cases": []})
    knowledge = build_knowledge(tmp_path / "k.json", MockLLM([mock_response()]), holdout_path)
    assert len(knowledge["discarded_rules"]) == 19
    assert all(r["level"] == "conversation" for r in knowledge["rules"])


@pytest.mark.parametrize("bad", ["check", "evidence", "missing"])
def test_invalid_llm_rules_fail(tmp_path, bad, holdout_path):
    response = mock_response()
    if bad == "check":
        response["rules"][0]["check"] = {"eval": "True"}
    elif bad == "evidence":
        response["rules"][0]["evidence"] = "invented policy"
    else:
        response["rules"].pop()
    with pytest.raises(ValueError):
        build_knowledge(tmp_path / "k.json", MockLLM([response, {"evidence": {}}]), holdout_path)


def test_policy_paragraph_grounding(tmp_path, holdout_path):
    response = mock_response()
    for rule in response["rules"]:
        rule["evidence"] = "paraphrased quotation"
    llm = MockLLM([response, {"evidence": {r["rule_id"]: "P0" for r in response["rules"]}}])
    knowledge = build_knowledge(tmp_path / "k.json", llm, holdout_path)
    assert llm.usage["calls"] == 2
    assert all(r["evidence"] == "# Retail agent policy" for r in knowledge["rules"])


def test_disk_cache_and_accounting(tmp_path, monkeypatch):
    import litellm
    calls = []
    def completion(**request):
        calls.append(request)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer": 42}',
            model_dump=lambda **kwargs: {"content": '{"answer": 42}'}))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3, model_dump=lambda: {"prompt_tokens": 4, "completion_tokens": 3}),
            _hidden_params={"response_cost": 0.01})
    monkeypatch.setattr(litellm, "completion", completion)
    client = LiteLLMClient("openai/mock", 0, 0, tmp_path, api_key="test-only-secret")
    messages = [{"role": "user", "content": "test"}]
    assert client.json(messages) == {"answer": 42}
    other = LiteLLMClient("openai/mock", 0, 0, tmp_path, api_key="test-only-secret")
    assert other.json(messages) == {"answer": 42}
    assert len(calls) == 1 and other.usage["cache_hits"] == 1
    assert client.usage["cost_usd"] == 0.01 and client.usage["prompt_tokens"] == 4
    assert "test-only-secret" not in next(tmp_path.glob("*.json")).read_text()
    LiteLLMClient("openai/mock", 0, 1, tmp_path).json(messages)
    assert len(calls) == 2  # seed participates in the cache key
