"""Stage 3b - Verifier: environment-grounded admission checks (PLAN Step 3).

[BLOG] A task joins the dataset only once the environment establishes that its
reference outcome is reachable and is the outcome the policy calls for. An LLM's
judgment is never sufficient. Failing tasks are diagnosed (structured cause).
"""

from pioneer.schemas import VerifierReport


def verify(task, meta, knowledge: dict) -> VerifierReport:
    """Run checks V1-V8 (V9 optional). TODO(PLAN Step 3)."""
    raise NotImplementedError("PLAN Step 3")
