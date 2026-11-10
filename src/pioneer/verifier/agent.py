"""[BLOG] Verifier agent gathers evidence; [INDEPENDENT] program owns admission."""
import json

from pioneer.explorer.agent import function
from .evidence import EvidenceEngine

TOOLS = [function("check_evidence", "Run/inspect one environment evidence check. Results are cached for this candidate; E8 is not rerun on repeated calls.",
                  {"name": {"type": "string", "enum": [f"E{i}" for i in range(1, 10)]}}, ["name"]),
         function("submit_report", "Submit diagnosis [{check,cause}]. Each diagnosis must cite an inspected failed check. The program computes passed; your verdict cannot override it.",
                  {"diagnosis": {"type": "array"}}, ["diagnosis"])]


# Module-level so the domain-free prompt test can inspect it (identical text to
# the previous inline string, so cached Step 3 responses stay valid).
SYSTEM = (
    "You are the Verifier agent [BLOG]. Inspect every E1-E9 tool result, diagnose failures using concrete "
    "steps/rule IDs/arguments from those results, then submit_report. Environment checks alone determine "
    "admission. You cannot waive failed checks or promote an unreachable task. E8 may be blocked by earlier "
    "failures; diagnose the actual root cause. Batch checks. Do not invent evidence.")


class VerifierAgent:
    def __init__(self, llm, engine):
        self.llm, self.engine = llm, engine

    def run(self, task, meta, max_turns=8):
        inspected, trace = set(), []
        result = None
        messages = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(dict(task=task.model_dump(mode="json"), meta=meta.model_dump(mode="json"),
                world_model_rules=[{k:r[k] for k in ("rule_id", "tools", "text", "check", "classification")}
                                   for r in self.engine.knowledge.get("rules", [])]), ensure_ascii=False)}]
        for turn in range(max_turns):
            message = self.llm.chat(messages, TOOLS)
            messages.append(message)
            for call in message.get("tool_calls", []):
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"])
                    if name == "check_evidence":
                        if self.engine.report is None:
                            self.engine.evaluate(task, meta)
                        item = next(c for c in self.engine.report.checks if c.name == args["name"])
                        inspected.add(item.name)
                        response = item.model_dump(mode="json")
                    elif name == "submit_report":
                        if inspected != {f"E{i}" for i in range(1, 10)}:
                            raise ValueError("inspect every E1-E9 check before submission")
                        diagnosis = args["diagnosis"]
                        failed = {c.name for c in self.engine.report.checks if not c.passed}
                        if any(d.get("check") not in failed or not d.get("cause") for d in diagnosis):
                            raise ValueError("diagnosis must cite an inspected failed check")
                        result = self.engine.report.model_copy(deep=True)
                        result.diagnosis["agent"] = diagnosis
                        # An optional/model-invented passed key is deliberately ignored.
                        response = dict(passed=result.passed, failure_codes=[c.value for c in result.failure_codes])
                    else:
                        raise ValueError("unknown verifier tool")
                except (ValueError, KeyError, TypeError, StopIteration) as exc:
                    response = dict(error=f"{type(exc).__name__}: {exc}")
                messages.append(dict(role="tool", tool_call_id=call["id"], content=json.dumps(response, ensure_ascii=False)))
                trace.append(dict(turn=turn, name=name, arguments=call["function"]["arguments"], response=response))
            if result is not None:
                result.diagnosis["agent_trace"] = trace
                return result
        raise RuntimeError("Verifier agent budget exhausted without a report")
