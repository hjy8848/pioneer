"""[BLOG] Ground, verify, and rework one scenario until it passes or the budget ends.

"Tasks that fail are not thrown away. They are diagnosed and reworked until they
pass." [INDEPENDENT] A per-batch round budget bounds the spending; what is not
fixed is returned to the caller for the rework queue, never dropped.

Shared by the Evolver driver. Each round builds a fresh client so generation and
validation spend can be told apart, exactly as scripts/initialize.py records it.
"""
import json

from pioneer.initializer import InitializerAgent
from pioneer.llm import LiteLLMClient
from pioneer.paths import HOLDOUT_DIR
from pioneer.verifier import verify

# After the client's own retries these mean the gateway is down: stop instead of burning rounds.
FATAL = {"RateLimitError", "AuthenticationError", "APIConnectionError", "InternalServerError",
         "ServiceUnavailableError", "BadGatewayError"}


def run_rounds(scenario, candidate_id, *, knowledge, params, verifier_cfg, seed_instances, append,
               ledger_factory, pool_size=20, rounds=5, start_round=0, parent=None,
               previous=None, diagnosis=None, atomic_action=None, archive=None):
    """Run up to ``rounds`` attempts. Returns (accepted_record | None, previous, diagnosis).

    ``archive(name, data)``, when given, receives each round's full Initializer conversation and
    the cache keys of every model call of the round (grounding, Verifier agent, E8 participants).
    """
    seen = set(seed_instances)

    def keep(round_no, initializer, client):
        if archive:
            archive(f"round{round_no}", dict(candidate_id=candidate_id, round=round_no,
                                             initializer_messages=initializer.messages, llm_calls=client.records))
    last_inputs = None
    for round_no in range(start_round, start_round + rounds):
        # [INDEPENDENT] A round fed the same proposal and diagnosis as the last one replays it (seeded
        # generation, disk cache), so the budget would be spent on copies: stop and queue the scenario.
        same = previous
        if isinstance(previous, dict) and isinstance(previous.get("meta"), dict):  # the round number itself
            same = dict(previous, meta={k: v for k, v in previous["meta"].items() if k != "repair_rounds"})
        inputs = json.dumps([same, diagnosis], sort_keys=True, default=str)
        if inputs == last_inputs:
            print(f"{candidate_id} round={round_no} would repeat round {round_no - 1}; stopping rework", flush=True)
            break
        last_inputs = inputs
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
            keep(round_no, initializer, client)
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
            keep(round_no, initializer, client)
            print(f"{candidate_id} {diagnosis}", flush=True)
            if type(exc).__name__ in FATAL:
                raise
    return None, previous, diagnosis


def compact_diagnosis(diagnosis, limit=3000):
    """What the Evolver needs from a failed round: the error or failed checks, not transcripts."""
    if not diagnosis:
        return None
    if "error" in diagnosis:
        failures = ((diagnosis.get("last_tool_result") or {}).get("failures") if isinstance(
            diagnosis.get("last_tool_result"), dict) else None) or []
        out = dict(error=diagnosis["error"], failed_checks=[
            dict(name=f.get("name"), details=json.dumps(f.get("details"), default=str)[:600]) for f in failures])
    else:
        out = dict(failure_codes=diagnosis.get("failure_codes"), failed_checks=[
            dict(name=c["name"], details=json.dumps({k: v for k, v in (c.get("details") or {}).items()
                                                      if k not in ("trials", "replay")}, default=str)[:600])
            for c in diagnosis.get("checks", []) if not c.get("passed")])
    text = json.dumps(out, default=str)
    return out if len(text) <= limit else dict(summary=text[:limit])


def run_with_rewrites(scenario, candidate_id, *, revise, rewrites, append, start_round=0,
                      previous=None, diagnosis=None, **round_options):
    """[INDEPENDENT] Rounds of grounding rework; when they end without admission, the Evolver
    rewrites the scenario from the diagnosis (``revise(scenario, diagnosis) -> Scenario``), at most
    ``rewrites`` times. Returns (accepted_record | None, previous, diagnosis, final scenario)."""
    from pioneer.initializer.contract import scenario_key
    last = [start_round - 1]

    def counted(record):
        last[0] = max(last[0], record["round"])
        append(record)

    for attempt in range(rewrites + 1):
        record, previous, diagnosis = run_rounds(scenario, candidate_id, append=counted, start_round=last[0] + 1,
            previous=previous, diagnosis=diagnosis, atomic_action=scenario_key(scenario), **round_options)
        if record is not None or attempt == rewrites:
            return record, previous, diagnosis, scenario
        print(f"{candidate_id} scenario not admitted; Evolver rewrite {attempt + 1}/{rewrites}", flush=True)
        try:
            scenario = revise(scenario, compact_diagnosis(diagnosis))
        except Exception as exc:
            if type(exc).__name__ in FATAL:
                raise
            print(f"{candidate_id} Evolver rewrite failed ({type(exc).__name__}: {exc}); queueing", flush=True)
            return None, previous, diagnosis, scenario
        previous = diagnosis = None  # a new scenario starts its grounding from scratch
    return record, previous, diagnosis, scenario
