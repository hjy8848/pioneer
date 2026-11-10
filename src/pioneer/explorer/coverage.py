"""[INDEPENDENT] Policy paragraph coverage, measured from literal source evidence."""
import json
from pathlib import Path


def policy_coverage(policy, rules, conversation_rules, explanations=None):
    paragraphs = {f"P{i}": text for i, text in enumerate(policy.split("\n\n")) if text.strip()}
    entries = []
    for pid, text in paragraphs.items():
        matches = [r["rule_id"] for r in rules + conversation_rules
                   if r.get("policy_quote") and
                   (r["policy_quote"] in text or text in r["policy_quote"])]
        entries.append(dict(paragraph_id=pid, text=text, rule_ids=matches,
                            reason=None if matches else (explanations or {}).get(pid, "No confirmed rule maps to this paragraph.")))
    covered = sum(bool(e["rule_ids"]) for e in entries)
    return dict(covered=covered, total=len(entries), fraction=covered / len(entries) if entries else 1.0,
                paragraphs=entries)


def save_world_model(path, result):
    """Keep bulky replay provenance separate from downstream world knowledge."""
    path = Path(path)
    trace = {key: result.pop(key, []) for key in ("agent_trace", "llm_requests")}
    previous = result.pop("_previous_trace", None)
    if previous:
        trace["previous"] = previous
    trace_path = path.with_name("retail_explorer_trace.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
