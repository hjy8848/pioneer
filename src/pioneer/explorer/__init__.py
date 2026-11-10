"""Stage 1 - Explorer: build structured world knowledge (PLAN Step 2).

[BLOG] An explorer agent reads tool code, state schema, seeded DB and policy,
and compiles what each action needs, what it changes, and how it can fail.
"""

from .build import build_knowledge

__all__ = ["build_knowledge"]
