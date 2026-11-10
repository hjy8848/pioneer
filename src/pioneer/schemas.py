"""Shared data contracts between pipeline stages.

Enum members marked [BLOG] map onto terms the AWS blog uses; their precise
definitions are [INDEPENDENT] (the blog does not publish them). Codex steps may
extend these, but should not rename existing members without updating docs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Stressor(str, Enum):
    """Evolution stressors (blog Stage 2)."""

    CHAIN = "chain"  # [BLOG] more actions chained together
    MULTI_ENTITY = "multi_entity"  # [BLOG] more entities involved
    POLICY_PUSHBACK = "policy_pushback"  # [BLOG] user pushes back on policy
    CONDITIONAL = "conditional"  # [BLOG] user asks for something conditional
    UNDERSPECIFIED = "underspecified"  # [BLOG] under-specified request
    REASONING = "reasoning"  # [BLOG] reasoning-level difficulty


class ExpectedOutcome(str, Enum):
    """Outcome classes tracked by the global coverage tracker [BLOG: incl. 'say no']."""

    COMPLETE = "complete"  # all requested writes are policy-compliant and done
    PARTIAL = "partial"  # compliant part done, forbidden part refused
    REFUSE = "refuse"  # nothing may change; correct behaviour is to say no
    TRANSFER = "transfer"  # out of scope -> transfer_to_human_agents
    INFORM = "inform"  # read-only; answer goes into communicate_info


class FailureCode(str, Enum):
    """Verifier failure codes (see docs/PLAN.md Step 3)."""

    TOOL_ERROR = "TOOL_ERROR"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    NO_STATE_CHANGE = "NO_STATE_CHANGE"
    UNEXPECTED_DIFF = "UNEXPECTED_DIFF"
    ARG_NOT_DERIVABLE = "ARG_NOT_DERIVABLE"
    ID_LEAK = "ID_LEAK"
    REFUSAL_NOT_FORBIDDEN = "REFUSAL_NOT_FORBIDDEN"
    AMBIGUITY_INVALID = "AMBIGUITY_INVALID"
    REWARD_MISMATCH = "REWARD_MISMATCH"
    UNREACHABLE_E2E = "UNREACHABLE_E2E"
    DUPLICATE = "DUPLICATE"
    HOLDOUT_LEAK = "HOLDOUT_LEAK"


class Intent(BaseModel):
    """One atomic action a scenario asks for, with constraints on its entities."""

    atomic_action: str  # e.g. "cancel_pending_order" or "inform:order_status"
    entity_constraints: list[tuple[str, str, Any]] = Field(
        default_factory=list,
        description="query_data clauses, e.g. [('status','eq','pending')]",
    )
    notes: Optional[str] = None


class Scenario(BaseModel):
    """Abstract scenario produced by the Evolver, before grounding."""

    scenario_id: str
    depth: int = 0
    parent_id: Optional[str] = None
    stressors: list[Stressor] = Field(default_factory=list)
    intents: list[Intent]
    expected_outcome: ExpectedOutcome
    conditions: Optional[dict] = None
    ambiguity: Optional[dict] = None
    rationale: Optional[str] = None


class CheckResult(BaseModel):
    name: str  # e.g. "V1_executable"
    passed: bool
    details: dict = Field(default_factory=dict)


class VerifierReport(BaseModel):
    passed: bool
    checks: list[CheckResult] = Field(default_factory=list)
    failure_codes: list[FailureCode] = Field(default_factory=list)
    diagnosis: dict = Field(
        default_factory=dict,
        description="structured, program-generated cause (step, rule, argument)",
    )


class TaskMeta(BaseModel):
    """Synthesis metadata stored next to each tau2 Task."""

    scenario_id: str
    depth: int
    parent_id: Optional[str] = None
    stressors: list[Stressor] = Field(default_factory=list)
    expected_outcome: ExpectedOutcome
    structure_signature: str
    instance_signature: str
    num_write_actions: int
    repair_rounds: int = 0
    verifier_report: Optional[VerifierReport] = None
    # [INDEPENDENT] Machine-checkable provenance for E4/E5/E6. Expressions
    # reference literal user text or selectors over actual READ tool outputs.
    read_steps: list[dict] = Field(default_factory=list)
    argument_bindings: dict[str, dict] = Field(default_factory=dict)
    communicate_bindings: list[dict] = Field(default_factory=list)
    refusal_requests: list[dict] = Field(default_factory=list)
    ambiguity: Optional[dict] = None
    requested_capability: Optional[str] = None
