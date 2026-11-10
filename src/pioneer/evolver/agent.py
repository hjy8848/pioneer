"""[BLOG] Stage 2: the Evolver agent applies one evolution stressor to a verified task.

"Each generation takes existing scenarios and applies evolution stressors to
produce harder descendants." The agent only designs the scenario; the Initializer
grounds it in real entities and the Verifier checks it against the environment.
[INDEPENDENT] Tool protocol, per-stressor shape checks and prompt are ours.
"""
import json
from collections import Counter

from pioneer.explorer import ExplorerAgent
from pioneer.explorer.agent import function
from pioneer.schemas import ExpectedOutcome, Scenario, Stressor

TOOLS = [
    function("query_data", "Inspect real non-holdout entities. where is [[field,op,value],...].",
             {"collection": {"type": "string"}, "where": {"type": "array"}, "limit": {"type": "integer"}}, ["collection"]),
    function("sandbox_execute", "Try a native tool against a fresh environment; cannot modify disk.",
             {"tool": {"type": "string"}, "arguments": {"type": "object"}}, ["tool", "arguments"]),
    function("submit_scenario", "Submit the descendant scenario. The program checks its shape; the Verifier later checks it against the data.",
             {"scenario": {"type": "object"}}, ["scenario"]),
]

# [BLOG] "The recipe never needs to know what your domain is": role, formats and the
# stressor definitions only; policy, tools, rules and entities arrive in the payload.
SYSTEM = """You are the {domain} Evolver agent [BLOG]. payload.parent is a verified task and
payload.stressor is the evolution stressor to apply. Design one harder descendant scenario and
submit it with submit_scenario. You write the scenario only: an Initializer will ground it in
real entities and a Verifier will check it against the environment, so propose only what the
data supports, and test it first with query_data and sandbox_execute. Business constraints come
only from the supplied policy, tool schemas and verified world-model rules.
Scenario: {intents:[{atomic_action,notes}],expected_outcome,conditions,ambiguity,rationale}.
atomic_action is one of payload.atomic_actions. expected_outcome is complete, partial, refuse,
transfer or inform. The descendant must be harder than the parent in exactly the way the
stressor says, stay solvable under the policy, and keep the same user as the parent.
Stressors:
- chain: keep the parent's intents and add at least one intent of an atomic action the parent
  does not have.
- multi_entity: keep the parent's intents and add intents of atomic actions the parent already
  has, on further entities of the same user.
- policy_pushback: the user insists on a request the rules forbid. expected_outcome is refuse
  (nothing may change) or partial (the permitted part is done, the forbidden part refused).
  Confirm with sandbox_execute that the forbidden request is rejected or breaks a listed rule.
- conditional: set conditions={collection,where,holds,if_true,if_false}. where is the condition
  as [[field,op,value]] clauses over the collection, holds says whether it is true in the
  initial data (check with query_data), if_true and if_false are the two branch tool names.
- underspecified: set ambiguity={collection,initial_where,clarified_where}: the user's first
  description matches several records and, once the user clarifies when asked, exactly one.
- reasoning: the user's goal needs a comparison or computation over real data, for example
  choosing by an extreme value, so the answer is not stated outright. Describe it in the notes
  of at least one intent.
payload.gaps lists what earlier tasks have not yet covered; prefer a design that fills a gap.
Always give a rationale. Submit once the scenario is checked; fix reported problems and resubmit.
"""


def build_system(metadata):
    return SYSTEM.replace("{domain}", metadata["domain"])


def validate_scenario(parent: Scenario, stressor: Stressor, scenario: Scenario, atomic_actions, max_depth: int) -> None:
    """Shape checks only; whether the scenario is real is the Verifier's job."""
    if parent.depth + 1 > max_depth:
        raise ValueError(f"depth {parent.depth + 1} exceeds the maximum {max_depth}")
    if not scenario.intents:
        raise ValueError("a scenario needs at least one intent")
    unknown = sorted({i.atomic_action for i in scenario.intents} - set(atomic_actions))
    if unknown:
        raise ValueError(f"unknown atomic actions {unknown}; use payload.atomic_actions")
    if not (scenario.rationale or "").strip():
        raise ValueError("give a rationale")
    old, new = Counter(i.atomic_action for i in parent.intents), Counter(i.atomic_action for i in scenario.intents)
    described = lambda s: (Counter((i.atomic_action, i.notes or "") for i in s.intents), s.expected_outcome, s.conditions, s.ambiguity)
    if described(scenario) == described(parent):  # notes count: a reasoning child may only change how the goal is posed
        raise ValueError("the scenario equals its parent; apply the stressor")
    added = new - old
    if stressor == Stressor.CHAIN and not set(added) - set(old):
        raise ValueError("chain must add an intent of an atomic action the parent does not have")
    if stressor == Stressor.MULTI_ENTITY and (not added or set(added) - set(old)):
        raise ValueError("multi_entity must add only intents of atomic actions the parent already has")
    if stressor == Stressor.POLICY_PUSHBACK and scenario.expected_outcome not in (ExpectedOutcome.REFUSE, ExpectedOutcome.PARTIAL):
        raise ValueError("policy_pushback needs expected_outcome refuse or partial")
    if stressor == Stressor.CONDITIONAL:
        spec = scenario.conditions or {}
        if not {"collection", "where", "holds", "if_true", "if_false"} <= set(spec):
            raise ValueError("conditional needs conditions={collection,where,holds,if_true,if_false}")
        if {spec["if_true"], spec["if_false"]} - set(atomic_actions) or spec["if_true"] == spec["if_false"]:
            raise ValueError("if_true and if_false must be two different atomic actions from payload.atomic_actions")
    if stressor == Stressor.UNDERSPECIFIED and not {"collection", "initial_where", "clarified_where"} <= set(scenario.ambiguity or {}):
        raise ValueError("underspecified needs ambiguity={collection,initial_where,clarified_where}")
    if stressor == Stressor.REASONING and not any((i.notes or "").strip() for i in scenario.intents):
        raise ValueError("reasoning needs the required comparison or computation in an intent's notes")


class EvolverAgent:
    def __init__(self, llm, knowledge, holdout_path, max_depth=4):
        self.llm, self.knowledge, self.max_depth = llm, knowledge, max_depth
        self.toolbox = ExplorerAgent(llm, holdout_path)
        self.trace = []

    def run(self, parent_row: dict, parent: Scenario, stressor: Stressor, scenario_id: str, gaps: dict, max_turns=12) -> Scenario:
        metadata = self.toolbox.metadata
        meta = {k: v for k, v in parent_row["meta"].items() if k != "verifier_report"}
        payload = dict(parent=dict(task=parent_row["task"], meta=meta, scenario=parent.model_dump(mode="json")),
            stressor=stressor.value, domain=metadata["domain"], atomic_actions=self.knowledge["atomic_actions"],
            tools=metadata["tools"], policy=metadata["policy"], gaps=gaps,
            rules=[{k: r[k] for k in ("rule_id", "tools", "text", "check")} for r in self.knowledge["rules"]])
        messages = [dict(role="system", content=build_system(metadata)), dict(role="user", content=json.dumps(payload))]
        self.trace = []
        for turn in range(max_turns):
            print(f"Evolver {scenario_id} turn={turn + 1}", flush=True)
            message = self.llm.chat(messages, TOOLS)
            messages.append(message)
            for call in message.get("tool_calls", []):
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"])
                    if name == "query_data":
                        response = self.toolbox.dispatch(name, args)
                    elif name == "sandbox_execute":
                        response = self.toolbox.observe(**args)
                    elif name == "submit_scenario":
                        draft = Scenario.model_validate(dict(args["scenario"], scenario_id=scenario_id,
                            depth=parent.depth + 1, parent_id=parent_row["task"]["id"],
                            stressors=[*parent.stressors, stressor]))
                        validate_scenario(parent, stressor, draft, self.knowledge["atomic_actions"], self.max_depth)
                        self.trace.append(dict(turn=turn, name=name, accepted=True))
                        return draft
                    else:
                        raise ValueError("unknown Evolver tool")
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    response = dict(error=f"{type(exc).__name__}: {exc}")
                self.trace.append(dict(turn=turn, name=name, arguments=call["function"]["arguments"], response=response))
                messages.append(dict(role="tool", tool_call_id=call["id"], content=json.dumps(response, default=str)))
            if not message.get("tool_calls"):
                messages.append(dict(role="user", content="Use the tools and call submit_scenario."))
        raise RuntimeError("Evolver budget exhausted without a scenario")
