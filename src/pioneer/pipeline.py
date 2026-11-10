"""[BLOG] Ground, verify, and rework one scenario until it passes or the budget ends.

"Tasks that fail are not thrown away. They are diagnosed and reworked until they
pass." [INDEPENDENT] A per-batch round budget bounds the spending; what is not
fixed is returned to the caller for the rework queue, never dropped.

Shared by the Evolver driver. Each round builds a fresh client so generation and
validation spend can be told apart, exactly as scripts/initialize.py records it.
"""
from pioneer.initializer import InitializerAgent
from pioneer.llm import LiteLLMClient
from pioneer.paths import HOLDOUT_DIR
from pioneer.verifier import verify

# After the client's own retries these mean the gateway is down: stop instead of burning rounds.
FATAL = {"RateLimitError", "AuthenticationError", "APIConnectionError", "InternalServerError",
         "ServiceUnavailableError", "BadGatewayError"}


def run_rounds(scenario, candidate_id, *, knowledge, params, verifier_cfg, seed_instances, append,
               ledger_factory, pool_size=20, rounds=5, start_round=0, parent=None,
               previous=None, diagnosis=None, atomic_action=None):
    """Run up to ``rounds`` attempts. Returns (accepted_record | None, previous, diagnosis)."""
    seen = set(seed_instances)
    for round_no in range(start_round, start_round + rounds):
        client = LiteLLMClient(**params)
        initializer = InitializerAgent(client, knowledge, HOLDOUT_DIR / "users.json")
        generation = None
        print(f"{candidate_id} round={round_no} generating", flush=True)
        try:
            task, meta = initializer.run(scenario, candidate_id, previous=previous, diagnosis=diagnosis,
                seen_instance=seen, ledger=ledger_factory(), pool_size=pool_size, parent=parent)
            generation = dict(client.usage)
            meta.repair_rounds = round_no
            report = verify(task, meta, knowledge, llm=client, seen_instance=seen,
                            trials=verifier_cfg["e2e_trials"], seed=params["seed"])
            record = dict(candidate_id=candidate_id, atomic_action=atomic_action, round=round_no, passed=report.passed,
                scenario=scenario.model_dump(mode="json"), task=task.model_dump(mode="json"),
                meta=meta.model_dump(mode="json"), report=report.model_dump(mode="json"),
                generation_usage=generation, validation_usage={k: client.usage[k] - generation[k] for k in generation},
                llm_usage=client.usage, initializer_trace=initializer.trace,
                preflight_submissions=initializer.submissions)
            append(record)
            print(f"{candidate_id} passed={report.passed} codes={[c.value for c in report.failure_codes]}", flush=True)
            if report.passed:
                return record, previous, diagnosis
            previous, diagnosis = dict(task=record["task"], meta=record["meta"]), report.model_dump(mode="json")
        except Exception as exc:
            previous = initializer.last_proposal or previous
            diagnosis = dict(error=f"{type(exc).__name__}: {exc}",
                             last_tool_result=initializer.trace[-1].get("response") if initializer.trace else None)
            append(dict(candidate_id=candidate_id, atomic_action=atomic_action, round=round_no, passed=False,
                scenario=scenario.model_dump(mode="json"), error=diagnosis, proposal=previous,
                generation_usage=generation or dict(client.usage),
                validation_usage={k: client.usage[k] - generation[k] for k in generation} if generation else {},
                llm_usage=dict(client.usage), initializer_trace=initializer.trace,
                preflight_submissions=initializer.submissions))
            print(f"{candidate_id} {diagnosis}", flush=True)
            if type(exc).__name__ in FATAL:
                raise
    return None, previous, diagnosis
