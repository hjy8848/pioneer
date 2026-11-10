"""[BLOG] Explorer; [INDEPENDENT] bounded compilation and deterministic validation."""
import json
from collections import Counter
from pathlib import Path

from pioneer.env import RetailSynthEnv
from pioneer.paths import HOLDOUT_DIR, TAU2_DIR, TAU2_COMMIT
from .source import extract_models, extract_tools
from .rules import rule_catalog
from .probes import probe_rule


def atomic_actions(tools):
    """[INDEPENDENT] WRITE atoms, answerable READ projections and transfer."""
    atoms = [dict(name=name, tool=name, kind="write", source="[OFFICIAL]")
             for name, tool in tools.items() if tool["tool_type"] == "write"]
    for name, collection, field, via in [
        ("order_status", "orders", "status", "get_order_details"),
        ("order_items", "orders", "items", "get_order_details"),
        ("product_options", "products", "variants", "get_product_details"),
        ("item_availability", "variants", "available", "get_item_details"),
        ("item_price", "variants", "price", "get_item_details"),
        ("user_profile", "users", "address", "get_user_details"),
        ("gift_card_balance", "users", "payment_methods.{payment_method_id}.balance", "get_user_details"),
    ]:
        atoms.append(dict(name=f"inform:{name}", kind="inform", tool=via,
                          answer=dict(collection=collection, field=field, target="communicate_info"), source="[INDEPENDENT]"))
    atoms.append(dict(name="transfer_to_human_agents", tool="transfer_to_human_agents",
                      kind="transfer", scope="outside supported capabilities", source="[OFFICIAL]"))
    return atoms


def build_knowledge(out_path: Path, llm, holdout_path=HOLDOUT_DIR / "users.json"):
    holdout = set(json.loads(Path(holdout_path).read_text(encoding="utf-8")))
    metadata = RetailSynthEnv().get_metadata()
    root = TAU2_DIR / "src/tau2/domains/retail"
    tools = extract_tools(root / "tools.py")
    models = extract_models(root / "data_model.py")
    catalog = rule_catalog()
    actions = {k: v for k, v in tools.items() if v["tool_type"]}
    prompt = dict(policy=metadata["policy"], tools=actions, rule_templates=catalog)
    response = llm.json([
        {"role": "system", "content": "You extract Retail environment knowledge. Return JSON only. "
         "Return actions mapping EVERY supplied tool name to {requires: [str], changes: [str], failure_modes: [str]}. "
         "Return rules list covering EVERY supplied template exactly once, with rule_id, tools, text, check, level. "
         "Keep template checks and levels unchanged (they are the supported query_data DSL); rewrite text from policy "
         "and add evidence containing an exact substring of the policy for each rule. Conversation checks are null. "
         "Do not judge whether a tool enforces a rule. No official evaluation tasks are supplied."},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ])
    summaries = response["actions"]
    if set(summaries) != set(actions):
        raise ValueError("LLM must describe all and only the exposed tools")
    for summary in summaries.values():
        for key in ("requires", "changes", "failure_modes"):
            if not isinstance(summary.get(key), list) or not all(isinstance(x, str) for x in summary[key]):
                raise ValueError(f"invalid action summary: {key}")
    rules = response["rules"]
    if len(rules) != len(catalog) or {r["rule_id"] for r in rules} != {r["rule_id"] for r in catalog}:
        raise ValueError("unsupported or missing policy templates; extend compiler and paired probes explicitly")
    if any(not r.get("evidence") or r["evidence"] not in metadata["policy"] for r in rules):
        # Ground paraphrased quotations by selecting source paragraph IDs once.
        paragraphs = {f"P{i}": p for i, p in enumerate(metadata["policy"].split("\n\n")) if p.strip()}
        grounding = llm.json([
            {"role": "system", "content": "Map each rule to the supplied policy paragraph that supports it. "
             "Return JSON {evidence: {rule_id: paragraph_id}} covering every rule. "
             "Use exact supplied paragraph IDs, not quotes or paraphrases."},
            {"role": "user", "content": json.dumps(dict(paragraphs=paragraphs,
                rules=[dict(rule_id=r["rule_id"], text=r["text"]) for r in rules]))},
        ])["evidence"]
        if set(grounding) != {r["rule_id"] for r in rules} or any(p not in paragraphs for p in grounding.values()):
            raise ValueError("policy grounding must reference supplied paragraph IDs")
        for rule in rules:
            rule["evidence_id"] = grounding[rule["rule_id"]]
            rule["evidence"] = paragraphs[rule["evidence_id"]]
    templates = {r["rule_id"]: r for r in catalog}
    accepted, dropped = [], []
    for rule in rules:
        template = templates[rule["rule_id"]]
        for key in ("check", "level", "tools"):
            if rule[key] != template[key]:
                raise ValueError(f"unsupported {key} in {rule['rule_id']}")
        if not rule.get("evidence") or rule["evidence"] not in metadata["policy"]:
            raise ValueError(f"policy evidence is not a source substring: {rule['rule_id']}")
        # LLM prose cannot override the compiler's probe strategy or item scope.
        rule = dict(template, text=rule["text"], evidence=rule["evidence"],
                    evidence_id=rule.get("evidence_id"), source="[BLOG]", compiler_source="[INDEPENDENT]")
        if rule["level"] == "conversation":
            rule["classification"] = "conversation_only"
        else:
            probe = probe_rule(rule, holdout)
            rule.update(classification=probe["classification"], probe=probe)
        (dropped if rule["classification"] == "contradicted" else accepted).append(rule)
    counts = dict(Counter(r["classification"] for r in accepted + dropped))
    output = dict(tau2_commit=TAU2_COMMIT, tools=tools, models=models, db_schema=metadata["db_schema"],
                  db_stats=metadata["db_stats"], policy=metadata["policy"], action_summaries=summaries,
                  rules=accepted, discarded_rules=dropped, rule_counts=counts,
                  atomic_actions=atomic_actions(tools), llm_usage=dict(llm.usage), holdout_users=len(holdout),
                  llm_config=getattr(llm, "params", {"model": "MockLLM"}),
                  llm_requests=getattr(llm, "records", []),
                  limitation="Bounded policy templates; paired probes establish local behavior, not exhaustive proofs. "
                  "Conversation rules need trajectory checks. Synthetic fixture snapshots are recorded.")
    # JSON object keys (including integer-valued histogram bins) have one form.
    output = json.loads(json.dumps(output))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
