"""Stage 1 - Explorer: build structured world knowledge (PLAN Step 2).

[BLOG] An explorer agent reads tool code, state schema, seeded DB and policy,
and compiles what each action needs, what it changes, and how it can fail.
"""

from pathlib import Path


def build_knowledge(out_path: Path, llm=None) -> dict:
    """Produce data/knowledge/retail_knowledge.json. TODO(PLAN Step 2)."""
    raise NotImplementedError("PLAN Step 2")
