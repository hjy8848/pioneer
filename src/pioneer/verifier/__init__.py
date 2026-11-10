"""Stage 3b - Verifier: environment-grounded admission checks (PLAN Step 3).

[BLOG] A task joins the dataset only once the environment establishes that its
reference outcome is reachable and is the outcome the policy calls for. An LLM's
judgment is never sufficient. Failing tasks are diagnosed (structured cause).
"""

from .agent import VerifierAgent
from .evidence import EvidenceEngine, task_signatures


def verify(task, meta, knowledge: dict, *, llm, **engine_options):
    """Use the shared model agent to gather all mandatory E1-E9 evidence."""
    import json
    from pioneer.paths import HOLDOUT_DIR
    for option, filename in (("holdout", "users.json"), ("official_signatures", "signatures.json")):
        if option not in engine_options:
            engine_options[option] = json.loads((HOLDOUT_DIR / filename).read_text(encoding="utf-8"))
    return VerifierAgent(llm, EvidenceEngine(knowledge, llm=llm, **engine_options)).run(task, meta)


__all__ = ["VerifierAgent", "EvidenceEngine", "task_signatures", "verify"]
