"""[INDEPENDENT] Generation-ledger statistics with two first-pass definitions.

The Initializer self-repairs against deterministic preflight before anything is
handed to the Verifier, so "passed on round 0" alone overstates how often a
first attempt was good. We report both:

* ``outer``     - first pass = admitted in round 0 (no Verifier-triggered rework);
* ``with_preflight`` - first pass additionally requires zero failed preflight
  submissions, i.e. preflight self-repair counts as rework.

Rows are candidate attempts as written to candidates.jsonl: ``candidate_id,
atomic_action, round, passed, report?, generation_usage, validation_usage,
llm_usage, preflight_submissions?``. Rows from before submissions were logged
have no ``preflight_submissions`` and are reported separately, not guessed.
"""
from collections import Counter


def _cost(usage):
    return (usage or {}).get("cost_usd", 0) or 0


def summarize_ledger(rows, atomic_actions=None, requested=None):
    rows = list(rows)
    accepted = {}
    for row in rows:
        if row.get("passed"):
            accepted.setdefault(row["candidate_id"], row)
    by_candidate = {}
    for row in rows:
        by_candidate.setdefault(row["candidate_id"], []).append(row)

    def failed_submissions(row):
        return [s for s in (row.get("preflight_submissions") or []) if not s["passed"]]

    logged = [r for r in accepted.values() if r.get("preflight_submissions") is not None]
    clean = [r for r in logged if r["round"] == 0 and not failed_submissions(r)]
    failures_per_task = [sum(len(failed_submissions(r)) for r in by_candidate[cid]) for cid in
                         (r["candidate_id"] for r in logged)]
    first_submissions = [r["preflight_submissions"][0]["passed"] for r in rows if r.get("preflight_submissions")]
    total_cost = sum(_cost(r.get("llm_usage")) for r in rows)
    n = len(accepted)
    atoms = list(atomic_actions) if atomic_actions is not None else sorted({r["atomic_action"] for r in rows})
    summary = dict(
        accepted=n,
        requested=requested,
        first_pass=dict(
            outer=dict(count=sum(r["round"] == 0 for r in accepted.values()), of=n),
            with_preflight=dict(count=len(clean), of=len(logged), note="only tasks whose preflight log was recorded"),
        ),
        preflight=dict(
            tasks_with_log=len(logged),
            tasks_without_log=n - len(logged),
            mean_failed_submissions_per_accepted=(sum(failures_per_task) / len(logged)) if logged else None,
            first_submission_pass_rate=(sum(first_submissions) / len(first_submissions)) if first_submissions else None,
            failure_codes=dict(Counter(c for r in rows for s in (r.get("preflight_submissions") or [])
                                       for c in s.get("failure_codes", []))),
            stages=dict(Counter(s["stage"] for r in rows for s in (r.get("preflight_submissions") or []))),
        ),
        verifier_failure_codes=dict(Counter(c for r in rows for c in (r.get("report") or {}).get("failure_codes", []))),
        cost_usd=dict(
            generation=sum(_cost(r.get("generation_usage")) for r in rows if r["round"] == 0),
            validation=sum(_cost(r.get("validation_usage")) for r in rows if r["round"] == 0),
            rework=sum(_cost(r.get("llm_usage")) for r in rows if r["round"] > 0),
            total=total_cost,
            per_accepted=(total_cost / n) if n else None,
            projected_600=(total_cost / n * 600) if n else None,
        ),
        by_atomic={a: dict(
            candidates=len({r["candidate_id"] for r in rows if r["atomic_action"] == a}),
            accepted=sum(r["atomic_action"] == a for r in accepted.values()),
            first_pass_outer=sum(r["atomic_action"] == a and r["round"] == 0 for r in accepted.values()),
            first_pass_with_preflight=sum(r["atomic_action"] == a for r in clean),
            after_rework=sum(r["atomic_action"] == a and r["round"] > 0 for r in accepted.values()),
        ) for a in atoms},
    )
    return summary
