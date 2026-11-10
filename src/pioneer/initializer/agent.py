"""[BLOG] Initializer constructs references through environment tools, not templates."""
import json
from tau2.data_model.tasks import Task
from pioneer.schemas import TaskMeta
from pioneer.explorer import ExplorerAgent
from pioneer.explorer.agent import function
from pioneer.verifier import EvidenceEngine, task_signatures
from .requirements import WAIT_FOR_COMPLETION, require_completion_wait

TOOLS = [
    function("query_data", "Inspect real non-holdout entities. where is [[field,op,value],...].",
             {"collection": {"type": "string"}, "where": {"type": "array"}, "limit": {"type": "integer"}}, ["collection"]),
    function("sandbox_execute", "Try a native tool against a fresh environment; cannot modify disk.",
             {"tool": {"type": "string"}, "arguments": {"type": "object"}}, ["tool", "arguments"]),
    function("submit_candidate", "Submit native tau2 Task and TaskMeta. Deterministic preflight returns failures; E8 remains mandatory and pending.",
             {"task": {"type": "object"}, "meta": {"type": "object"}}, ["task", "meta"]),
]

SYSTEM = """You are the task Initializer agent [BLOG]. Ground the requested atomic intent
in real non-holdout entities through query_data and sandbox_execute. Construct your own
reference actions, natural user instructions, and machine-checkable READ provenance.
Do not use historical tasks or fixed reference templates. Read the supplied tool schemas.
Task: {id,user_scenario:{instructions:{domain:'retail',reason_for_call,known_info,
unknown_info,task_instructions}},evaluation_criteria:{actions:[{action_id,name,arguments}],
communicate_info:[],reward_basis:['DB'] or ['DB','COMMUNICATE']}}.
Meta requires scenario_id,depth=0,expected_outcome (complete/inform/transfer),
structure_signature='',instance_signature='',num_write_actions,read_steps,
argument_bindings,communicate_bindings. The caller fixes scenario_id/depth/outcome.
Every argument needs a binding keyed 'action_index.argument_name'. Expressions:
{source:'text',value:literal} must occur literally in user instructions (authentication
values in known_info); {source:'read',step:0,path:''} references actual prior READ output.
read_steps=[{tool:READ_name,arguments:{param:expression}}]. Identity lookup returns a
plain user-ID string; path='' uses it. Other READs return JSON objects.
To select an item ID list: {source:'read',step:N,path:'items',where:[['name','eq',
'exact product name in user text']],field:'item_id',many:true}. To select a single
payment: path:'payment_methods',where:[['source','eq','credit_card']],field:'id',many:false.
Selector operands must appear in user text; use the exact enum spelling if necessary.
Never use list indices to choose from multiple candidates. Specify enough user-facing
product options or card last-four digits to make selectors unique. Do NOT put internal
item IDs or payment IDs in user instructions. Order IDs and product IDs are allowed.
READs are not reference actions. Depth 0 WRITE tasks contain exactly the requested tool
once. Inform tasks use actions=[]; each communicate_info string must have a READ binding.
Use entity-specific communicate_info (e.g. order ID as well as status) to avoid repeating
identical grounded outcomes. Transfer tasks need one transfer action, requested_capability
absent from tools, whose underscore-separated words occur in user text as ordinary words.
For exchange/modify items, choose a unique available variant of the SAME product and a
payment method actually belonging to that user. Gift card must cover the difference.
For return use original payment or gift card. New address fields must be in user text
or derived from a READ. No initial_state patches in these depth-zero tasks.
Include the exact required completion-wait sentence in task_instructions. The user
must confirm the operation but NOT end at confirmation; wait for actual completion.
Inspect environment, trial the reference, submit. Repair reported preflight failures.
"""


class InitializerAgent:
    def __init__(self, llm, knowledge, holdout_path):
        self.llm, self.knowledge = llm, knowledge
        self.toolbox = ExplorerAgent(llm, holdout_path)
        self.trace = []
        self.queried = False
        self.last_proposal = None

    def run(self, scenario, candidate_id, *, previous=None, diagnosis=None, seen_instance=(), max_turns=20):
        summary = [{k:r[k] for k in ("rule_id", "tools", "text", "check")} for r in self.knowledge["rules"]]
        payload = dict(scenario=scenario.model_dump(mode="json"), candidate_id=candidate_id,
            tools=self.toolbox.metadata["tools"], policy=self.toolbox.metadata["policy"], rules=summary,
            required_sentence=WAIT_FOR_COMPLETION, previous=previous, diagnosis=diagnosis)
        messages = [dict(role="system", content=SYSTEM), dict(role="user", content=json.dumps(payload))]
        target = scenario.intents[0].atomic_action
        self.trace = []
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
                        if any(not c.passed for c in report.checks if c.name != "E8"):
                            response = dict(preflight_passed=False, admission_passed=False,
                                failures=[c.model_dump(mode="json") for c in report.checks if not c.passed and c.name != "E8"])
                        else:
                            meta.structure_signature, meta.instance_signature = task_signatures(task, meta)
                            self.trace.append(dict(turn=turn, name=name, accepted=True))
                            return task, meta
                    else:
                        raise ValueError("unknown Initializer tool")
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    response = dict(error=f"{type(exc).__name__}: {exc}")
                self.trace.append(dict(turn=turn, name=name, arguments=call["function"]["arguments"], response=response))
                messages.append(dict(role="tool", tool_call_id=call["id"], content=json.dumps(response, default=str)))
            if not message.get("tool_calls"):
                messages.append(dict(role="user", content="Use environment tools and submit_candidate."))
        raise RuntimeError("Initializer budget exhausted; retain this scenario in rework_queue")
