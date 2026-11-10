"""[BLOG] Initializer constructs references through environment tools, not templates."""
import json
from tau2.data_model.tasks import Task
from pioneer.env import RetailSynthEnv
from pioneer.schemas import TaskMeta
from pioneer.explorer import ExplorerAgent
from pioneer.explorer.agent import function
from pioneer.verifier import EvidenceEngine, task_signatures
from .diversity import candidate_pool
from .requirements import WAIT_FOR_COMPLETION, require_completion_wait

TOOLS = [
    function("query_data", "Inspect real non-holdout entities. where is [[field,op,value],...].",
             {"collection": {"type": "string"}, "where": {"type": "array"}, "limit": {"type": "integer"}}, ["collection"]),
    function("sandbox_execute", "Try a native tool against a fresh environment; cannot modify disk.",
             {"tool": {"type": "string"}, "arguments": {"type": "object"}}, ["tool", "arguments"]),
    function("submit_candidate", "Submit native tau2 Task and TaskMeta. Deterministic preflight returns failures; E8 remains mandatory and runs later.",
             {"task": {"type": "object"}, "meta": {"type": "object"}}, ["task", "meta"]),
]

# [BLOG] "The recipe never needs to know what your domain is." This prompt holds
# only the role, the task/evidence formats and domain-independent requirements.
# Business constraints come from the payload (policy, tool schemas, verified
# world-model rules); identifier handling comes from the schema-derived
# payload.opaque_id_paths. {domain} is substituted at run time.
SYSTEM = """You are the task Initializer agent [BLOG]. Ground the requested atomic intent
in real non-holdout entities through query_data and sandbox_execute. Construct your own
reference actions, natural user instructions, and machine-checkable READ provenance.
Do not use historical tasks or fixed reference templates. Business constraints come only from
the supplied policy, tool schemas and verified world-model rules: follow them, assume no others.
Task: {id,user_scenario:{instructions:{domain:'{domain}',reason_for_call,known_info,
unknown_info,task_instructions}},evaluation_criteria:{actions:[{action_id,name,arguments}],
communicate_info:[],reward_basis:['DB'] or ['DB','COMMUNICATE']}}.
Meta requires scenario_id,depth=0,expected_outcome (complete/inform/transfer),
structure_signature='',instance_signature='',num_write_actions,read_steps,
argument_bindings,communicate_bindings. The caller fixes scenario_id/depth/outcome.
Every argument needs a binding keyed 'action_index.argument_name'. Expressions:
{source:'text',value:literal} must occur literally in user instructions (authentication
values in known_info); {source:'read',step:0,path:''} references actual prior READ output.
read_steps=[{tool:READ_name,arguments:{param:expression}}]. A READ that returns a plain
string is referenced with path=''; other READs return JSON objects.
To select values from a READ output use {source:'read',step:N,path:'<dotted path>',
where:[[<field>,<op>,<operand>]],field:'<field to return>',many:true|false}. many:false
requires exactly one match. Selector operands must appear literally in the user text; use
the exact enum spelling if necessary. Never use list indices to choose from multiple
candidates: give enough user-facing attributes to make every selector unique.
Opaque identifiers: payload.opaque_id_paths lists nested collections (derived from the
database schema) whose keys are machine identifiers a user would not state. Never write
those values in any user instruction field; the Verifier rejects tasks that do. Select such
entities by user-facing attributes and bind them through READ provenance. Identifiers of
top-level collections may appear in user text.
READs are not reference actions. Depth 0 WRITE tasks contain exactly the requested tool
once. Inform tasks use actions=[]; each communicate_info string must have a READ binding.
Include an entity-identifying value from the READ output in communicate_info so that
different tasks do not repeat identical grounded outcomes. Transfer-outcome tasks contain
exactly the requested transfer tool call; requested_capability must be a capability absent
from the tool list whose underscore-separated words occur in the user text as ordinary words.
Reference arguments must be satisfiable for the chosen entities under the supplied policy
and rules; check them with sandbox_execute before submitting. No initial_state patches in
these depth-zero tasks.
Diversity: payload.candidate_pool lists eligible entities that are not yet used (seeded
sample); choose from it unless you can show no pool entity fits. payload.reuse_limits names
users and (action, entity) pairs that preflight rejects.
Include the exact required completion-wait sentence in task_instructions. The user
must confirm the operation but NOT end at confirmation; wait for actual completion.
Inspect environment, trial the reference, submit. Repair reported preflight failures.
"""


def build_system(metadata):
    return SYSTEM.replace("{domain}", metadata["domain"])


class InitializerAgent:
    def __init__(self, llm, knowledge, holdout_path):
        self.llm, self.knowledge = llm, knowledge
        self.toolbox = ExplorerAgent(llm, holdout_path)
        self.trace = []
        self.queried = False
        self.last_proposal = None
        # [INDEPENDENT] Every submit_candidate attempt and why it failed, so the
        # "first-pass" statistics are not inflated by in-run self-repair.
        self.submissions = []

    def _pool(self, scenario, candidate_id, ledger, pool_size, seed):
        env = RetailSynthEnv()
        env.init()
        target = scenario.intents[0].atomic_action
        collection, pool, eligible = candidate_pool(env, self.knowledge, target, holdout=self.toolbox.holdout,
            ledger=ledger, seed=f"{seed}:{candidate_id}", size=pool_size)
        limits = dict(max_tasks_per_user=ledger.max_tasks_per_user,
                      users_at_limit=sorted(u for u, n in ledger.users.items() if n >= ledger.max_tasks_per_user),
                      used_entities_for_this_action=sorted(e for a, e in ledger.pairs if a == target))
        return dict(collection=collection, eligible_count=eligible, entities=pool), limits

    def run(self, scenario, candidate_id, *, previous=None, diagnosis=None, seen_instance=(), max_turns=20,
            ledger=None, pool_size=20, seed=0):
        summary = [{k:r[k] for k in ("rule_id", "tools", "text", "check")} for r in self.knowledge["rules"]]
        metadata = self.toolbox.metadata
        payload = dict(scenario=scenario.model_dump(mode="json"), candidate_id=candidate_id,
            domain=metadata["domain"], tools=metadata["tools"], policy=metadata["policy"], rules=summary,
            opaque_id_paths=metadata["opaque_id_paths"],
            required_sentence=WAIT_FOR_COMPLETION, previous=previous, diagnosis=diagnosis)
        if ledger is not None:
            payload["candidate_pool"], payload["reuse_limits"] = self._pool(scenario, candidate_id, ledger, pool_size, seed)
        messages = [dict(role="system", content=build_system(metadata)), dict(role="user", content=json.dumps(payload))]
        target = scenario.intents[0].atomic_action
        self.trace, self.submissions, self.queried = [], [], False
        for turn in range(max_turns):
            print(f"Initializer {candidate_id} turn={turn + 1}", flush=True)
            message = self.llm.chat(messages, TOOLS)
            messages.append(message)
            for call in message.get("tool_calls", []):
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"])
                    if name == "query_data":
                        response = self.toolbox.dispatch(name, args)
                        self.queried = True
                    elif name == "sandbox_execute":
                        response = self.toolbox.observe(**args)
                    elif name == "submit_candidate":
                        self.last_proposal = args
                        if not self.queried:
                            raise ValueError("query the environment before submitting")
                        task = Task.model_validate(args["task"])
                        meta = TaskMeta.model_validate(args["meta"])
                        task.id, meta.scenario_id = candidate_id, scenario.scenario_id
                        meta.depth, meta.expected_outcome = 0, scenario.expected_outcome
                        meta.stressors, meta.parent_id = scenario.stressors, scenario.parent_id
                        require_completion_wait(task)
                        if task.initial_state is not None:
                            raise ValueError("depth-zero initialization uses the seeded database without patches")
                        actual = [a.name for a in task.evaluation_criteria.actions or []]
                        expected = [] if target.startswith("inform:") else [target]
                        if actual != expected:
                            raise ValueError(f"reference tools must be {expected}")
                        meta.num_write_actions = sum(a.name != "transfer_to_human_agents" for a in task.evaluation_criteria.actions)
                        report = EvidenceEngine(self.knowledge, holdout=self.toolbox.holdout,
                            seen_instance=seen_instance).evaluate(task, meta, run_e8=False)
                        failed = [c for c in report.checks if not c.passed and c.name != "E8"]
                        if failed:
                            self.submissions.append(dict(turn=turn, stage="preflight", passed=False,
                                failed_checks=[c.name for c in failed], failure_codes=[c.value for c in report.failure_codes]))
                            response = dict(preflight_passed=False, admission_passed=False,
                                failures=[c.model_dump(mode="json") for c in failed])
                        else:
                            reason = None
                            if ledger is not None:
                                env = RetailSynthEnv()
                                env.init(task)
                                reason = ledger.violation(target, *ledger.entities_of(task, meta, env))
                            if reason:
                                self.submissions.append(dict(turn=turn, stage="diversity", passed=False,
                                    failed_checks=["DIVERSITY"], failure_codes=["DIVERSITY"]))
                                response = dict(preflight_passed=False, admission_passed=False, diversity=reason)
                            else:
                                meta.structure_signature, meta.instance_signature = task_signatures(task, meta)
                                self.submissions.append(dict(turn=turn, stage="accepted", passed=True,
                                    failed_checks=[], failure_codes=[]))
                                self.trace.append(dict(turn=turn, name=name, accepted=True))
                                return task, meta
                    else:
                        raise ValueError("unknown Initializer tool")
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    response = dict(error=f"{type(exc).__name__}: {exc}")
                    if name == "submit_candidate":
                        self.submissions.append(dict(turn=turn, stage="contract", passed=False,
                            failed_checks=["CONTRACT"], failure_codes=["CONTRACT"], error=response["error"][:300]))
                self.trace.append(dict(turn=turn, name=name, arguments=call["function"]["arguments"], response=response))
                messages.append(dict(role="tool", tool_call_id=call["id"], content=json.dumps(response, default=str)))
            if not message.get("tool_calls"):
                messages.append(dict(role="user", content="Use environment tools and submit_candidate."))
        raise RuntimeError("Initializer budget exhausted; retain this scenario in rework_queue")
