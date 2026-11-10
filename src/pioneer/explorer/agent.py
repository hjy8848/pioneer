"""[BLOG] Tool-using Explorer; [INDEPENDENT] evidence protocol and sandbox tools.

The agent selects what to inspect and constructs its own probes. No rule catalog
or prebuilt probe constructor is supplied to this loop. Programs execute and
validate its proposals; unconfirmed claims cannot be submitted.
"""
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from tau2.domains.retail.data_model import RetailDB
from pioneer.env import RetailSynthEnv
from pioneer.paths import TAU2_DIR, TAU2_DATA_DIR, TAU2_COMMIT, HOLDOUT_DIR
from .rules import check_rule
from .source import extract_tools, extract_models


def function(name, description, properties, required=()):
    return dict(type="function", function=dict(name=name, description=description,
        parameters=dict(type="object", properties=properties, required=list(required))))


TOOLS = [
    function("read_source", "Read allowlisted native Retail source or policy.",
             {"path": {"type": "string", "enum": ["tools.py", "data_model.py", "policy.md"]}}, ["path"]),
    function("get_metadata", "Get native policy, schema, tool names/types and database statistics.", {}),
    function("query_data", "Read seeded entities, excluding holdout users/orders. where contains [field,op,value] clauses.",
        {"collection": {"type": "string", "enum": ["users", "orders", "products", "variants"]},
         "where": {"type": "array"}, "limit": {"type": "integer"}}, ["collection"]),
    function("sandbox_execute", "Observe a tool in a fresh sandbox. Optional patches [{collection,id,changes}] change only a local DB copy; setup is [{tool,arguments}].",
        {"tool": {"type": "string"}, "arguments": {"type": "object"}, "patches": {"type": "array"}, "setup": {"type": "array"}}, ["tool", "arguments"]),
    function("probe_condition", "Propose a state precondition and two fresh cases. Program checks predicate true/false then executes both. Labels are computed, not supplied by you.",
        {"rule_id": {"type": "string"}, "tool": {"type": "string"}, "text": {"type": "string"},
         "policy_quote": {"type": "string"}, "check": {"type": "object"}, "per_item": {"type": "boolean"},
         "positive": {"type": "object"}, "negative": {"type": "object"}},
        ["rule_id", "tool", "text", "check", "positive", "negative"]),
    function("submit_world_model", "Submit actions mapping ALL tool names to requires/changes/failure_modes lists of {text,evidence:[id,...]}. Every requires/failure_modes claim must reference accepted probe IDs for that tool; changes must reference successful sandbox observations or probes. Also submit atomic_actions (names), conversation_rules [{rule_id,tools,text,check:null,level:conversation,policy_quote}].",
        {"actions": {"type": "object"}, "atomic_actions": {"type": "array", "items": {"type": "string"}},
         "conversation_rules": {"type": "array"}}, ["actions", "atomic_actions", "conversation_rules"]),
]


SYSTEM = """You are the Retail Explorer agent [BLOG]. Discover the environment through tools.
Read tools.py, data_model.py and policy.md, get_metadata, and query_data before submitting.
Describe all 16 tools. Every claimed precondition and failure mode must have a paired
probe you construct using real entities you find. Include positive observations of every
tool for changes. Never trust your own verdict: the program computes classifications.
Read the policy and inspect the source for gaps (including modification after item
modification and exchange to the identical item). Include positive and violating cases.
You may synthesize local DB patches to isolate one condition; never patch disk.
Positive/negative cases have arguments, optional patches and setup (as sandbox_execute).
Patches target collection users/orders/products, an existing id, and changed top-level
fields; identity keys cannot change. Use setup tool calls when testing action chains.
Policy quote must be an exact substring read from policy; omit it for tool-only facts.
State check DSL: {collection:'orders',where:[['order_id','eq',{'ref':'args.order_id'}],
['status','eq','pending']]} or {compare:[{'ref':'args.reason'},'in',
['no longer needed','ordered by mistake']]} or {all:[check,...]} / {any:[check,...]}.
Operators: eq,ne,gt,ge,lt,le,in,not_in,contains,len_eq,len_ge,exists.
Refs args/order/user/payment/original_payment/required_amount are grounded by query_data.
With per_item=true refs args.item_id,args.new_item_id,item.product_id iterate item pairs.
Check examples explain the grammar, not rules you must reproduce. Propose your own rules
from source. Unconfirmed claims are forbidden. Do not list every theoretical failure:
only describe mechanisms you can confirm with your own paired probes in this run.
For no-precondition tools (e.g. transfer), requires and failure_modes can be empty.
Conversation rules cannot be DB-verified: level=conversation,check=null,policy_quote.
Atomic actions must include all seven WRITE names, inform: types you can answer through
READ tools, and transfer_to_human_agents. Submit through submit_world_model, not prose.
Batch independent tool calls when useful. Reuse discovered entity values and evidence IDs.
Tool responses report errors; revise failed proposals. No official evaluation tasks exist
in the tool surface. Keep exploration focused; complete within the configured turn budget.
"""


class ExplorerAgent:
    def __init__(self, llm, holdout_path=HOLDOUT_DIR / "users.json"):
        self.llm = llm
        self.holdout = set(json.loads(Path(holdout_path).read_text(encoding="utf-8")))
        self.metadata = RetailSynthEnv().get_metadata()
        self.sources = {
            "tools.py": TAU2_DIR / "src/tau2/domains/retail/tools.py",
            "data_model.py": TAU2_DIR / "src/tau2/domains/retail/data_model.py",
            "policy.md": TAU2_DATA_DIR / "tau2/domains/retail/policy.md",
        }
        self.methods = extract_tools(self.sources["tools.py"])
        self.exposed = {t["name"] for t in self.metadata["tools"]}
        self.observations, self.probes, self.visited = {}, {}, set()
        self.result = None

    def safe_arguments(self, args):
        env = RetailSynthEnv()
        env.init()
        if args.get("user_id") in self.holdout:
            raise ValueError("holdout user excluded")
        if "email" in args or {"first_name", "last_name", "zip"} <= set(args):
            matches = env.query_data("users", predicate=lambda user:
                ("email" in args and user["email"].casefold() == args["email"].casefold()) or
                ({"first_name", "last_name", "zip"} <= set(args) and
                 user["name"]["first_name"].casefold() == args["first_name"].casefold() and
                 user["name"]["last_name"].casefold() == args["last_name"].casefold() and
                 user["address"]["zip"] == args["zip"]))
            if any(u["user_id"] in self.holdout for u in matches):
                raise ValueError("holdout identity lookup excluded")
        orders = env.query_data("orders", [("order_id", "eq", args.get("order_id"))])
        if orders and orders[0]["user_id"] in self.holdout:
            raise ValueError("holdout order excluded")

    def case(self, tool, arguments, patches=(), setup=()):
        if tool not in self.exposed:
            raise ValueError("unknown native tool")
        self.safe_arguments(arguments)
        env = RetailSynthEnv()
        env.init()
        raw = env.env.tools.db.model_dump()
        for patch in patches:
            collection, key, changes = patch["collection"], patch["id"], patch["changes"]
            if collection not in {"users", "orders", "products"}:
                raise ValueError("patch collection not allowed")
            old = raw[collection][key]
            if (collection == "users" and key in self.holdout) or (collection == "orders" and old["user_id"] in self.holdout):
                raise ValueError("holdout patch excluded")
            if set(changes) & {"user_id", "order_id", "product_id"}:
                raise ValueError("identity patches forbidden")
            old.update(changes)
        env.env.tools.db = RetailDB.model_validate(raw)
        for action in setup:
            self.safe_arguments(action["arguments"])
            result = env.execute(action["tool"], action["arguments"])
            if not result.ok:
                raise ValueError(f"setup failed: {result.error}")
        return env

    def observe(self, tool, arguments, patches=(), setup=()):
        env = self.case(tool, arguments, patches, setup)
        before = env.state(False)["hash"]
        result = env.execute(tool, arguments)
        record = dict(tool=tool, arguments=arguments, patches=patches, setup=setup,
                      initial_hash=before, execution=asdict(result))
        oid = f"observation_{len(self.observations)}"
        self.observations[oid] = record
        return dict(evidence_id=oid, **record)

    def probe(self, rule_id, tool, text, check, positive, negative, policy_quote="", per_item=False):
        if rule_id in self.probes:
            raise ValueError("rule ID already recorded; use a new ID for a revised probe")
        if policy_quote and policy_quote not in self.metadata["policy"]:
            raise ValueError("policy_quote must match source verbatim")
        rule = dict(rule_id=rule_id, tools=[tool], text=text, check=check, per_item=per_item, level="state")
        records = []
        for compliant, case in ((True, positive), (False, negative)):
            env = self.case(tool, **case)
            if check_rule(env, rule, case["arguments"]) != compliant:
                raise ValueError("predicate must pass positive and fail negative BEFORE execution")
            result = env.execute(tool, case["arguments"])
            records.append(dict(compliant=compliant, case=case, execution=asdict(result)))
        label = ("contradicted" if not records[0]["execution"]["ok"] else
                 "confirmed_by_tool" if not records[1]["execution"]["ok"] else
                 "policy_only" if policy_quote else "unconfirmed")
        record = dict(rule, policy_quote=policy_quote, classification=label, cases=records)
        self.probes[rule_id] = record
        return record

    def submit(self, actions, atomic_actions, conversation_rules):
        required = {"read_source:tools.py", "read_source:data_model.py", "read_source:policy.md", "get_metadata", "query_data"}
        if not required <= self.visited:
            raise ValueError(f"required discovery tools not used: {sorted(required - self.visited)}")
        if set(actions) != self.exposed:
            raise ValueError("actions must cover exactly all exposed tools")
        for tool, summary in actions.items():
            for kind in ("requires", "changes", "failure_modes"):
                if not isinstance(summary.get(kind), list):
                    raise ValueError("each action requires three claim lists")
                for claim in summary[kind]:
                    ids = claim.get("evidence", [])
                    if not claim.get("text") or not ids:
                        raise ValueError("every claim requires text and evidence IDs")
                    for eid in ids:
                        p, o = self.probes.get(eid), self.observations.get(eid)
                        if p and tool in p["tools"] and p["classification"] in {"confirmed_by_tool", "policy_only"}:
                            continue
                        if kind == "changes" and o and o["tool"] == tool and o["execution"]["ok"]:
                            continue
                        raise ValueError(f"unconfirmed claim for {tool}: {eid}")
                    if kind in {"requires", "failure_modes"}:
                        # A reference to a status probe does not also prove an
                        # unrelated prose claim about stock or payment balance.
                        # Publish the probed predicate itself as the active fact.
                        facts = [self.probes[eid] for eid in ids]
                        claim["text"] = "; ".join(p["text"] for p in facts)
                        claim["verified_checks"] = [p["check"] for p in facts]
                        claim["classifications"] = [p["classification"] for p in facts]
                    else:
                        executions = [self.probes[eid]["cases"][0]["execution"] if eid in self.probes
                                      else self.observations[eid]["execution"] for eid in ids]
                        claim["text"] = "Successful native execution; consult verified_outputs and DB hash changes."
                        claim["verified_outputs"] = [e["result"] for e in executions]
                        claim["db_changed"] = [e["hash_before"] != e["hash_after"] for e in executions]
            if not summary["changes"]:
                raise ValueError(f"provide a positive observation for {tool}")
        writes = {n for n, t in self.methods.items() if t["tool_type"] == "write"}
        if not writes <= set(atomic_actions) or "transfer_to_human_agents" not in atomic_actions or not any(a.startswith("inform:") for a in atomic_actions):
            raise ValueError("atomic list must cover WRITE tools, inform types and transfer")
        for rule in conversation_rules:
            if rule.get("level") != "conversation" or rule.get("check") is not None or not rule.get("policy_quote") or rule["policy_quote"] not in self.metadata["policy"]:
                raise ValueError("conversation rules require exact policy evidence and check=null")
        self.result = dict(actions=actions, atomic_actions=atomic_actions, conversation_rules=conversation_rules)
        return {"accepted": True}

    def dispatch(self, name, args):
        if name == "read_source":
            path = args["path"]
            if path not in self.sources:
                raise ValueError("source not allowlisted")
            result = self.sources[path].read_text(encoding="utf-8")
            self.visited.add(f"read_source:{path}")
            return result
        if name == "get_metadata":
            self.visited.add(name)
            return dict(policy=self.metadata["policy"], db_stats=self.metadata["db_stats"],
                        db_schema=self.metadata["db_schema"], tools=[dict(name=t["name"], type=t["type"]) for t in self.metadata["tools"]])
        if name == "query_data":
            env = RetailSynthEnv()
            env.init()
            collection = args["collection"]
            where = list(args.get("where", []))
            if collection in {"users", "orders"}:
                where.append(("user_id", "not_in", sorted(self.holdout)))
            self.visited.add(name)
            return env.query_data(collection, where, limit=min(args.get("limit", 3), 10))
        if name == "sandbox_execute":
            return self.observe(**args)
        if name == "probe_condition":
            return self.probe(**args)
        if name == "submit_world_model":
            return self.submit(**args)
        raise ValueError("unknown explorer tool")

    def run(self, out_path, max_turns=24):
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Build the evidence-backed Retail world model now."}]
        trace = []
        for turn in range(max_turns):
            print(f"Explorer turn {turn + 1}/{max_turns}", flush=True)
            message = self.llm.chat(messages, TOOLS)
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                messages.append({"role": "user", "content": "Use environment tools and submit_world_model; prose alone is not a submission."})
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"])
                    response = self.dispatch(name, args)
                except (ValueError, KeyError, TypeError, IndexError, StopIteration) as error:
                    response = {"error": f"{type(error).__name__}: {error}"}
                messages.append(dict(role="tool", tool_call_id=call["id"], content=json.dumps(response, ensure_ascii=False)))
                trace.append(dict(turn=turn, name=name, arguments=call["function"]["arguments"], error=response.get("error") if isinstance(response, dict) else None))
                print(f"  {name}: {'error' if isinstance(response, dict) and 'error' in response else 'ok'}", flush=True)
            if turn in {23, 31} and self.result is None:
                messages.append({"role": "user", "content":
                    "Finish exploration now. Batch any remaining probe_condition calls using cases already observed, "
                    "then submit_world_model. sandbox_execute IDs support changes only; requires and failure_modes "
                    "must reference accepted probe IDs. Do not claim mechanisms without a paired probe; leave "
                    "unconfirmed mechanisms out. Remember the address-after-modification and identical-exchange "
                    "policy gaps. Cover all 16 tools' observed changes, but do not keep adding speculative cases."})
            if self.result is not None:
                accepted = [p for p in self.probes.values() if p["classification"] in {"confirmed_by_tool", "policy_only"}]
                result = dict(self.result, rules=accepted, discarded_rules=[p for p in self.probes.values() if p not in accepted],
                    observations=self.observations, rule_counts=dict(Counter(p["classification"] for p in self.probes.values())),
                    tools=self.methods, models=extract_models(self.sources["data_model.py"]), db_schema=self.metadata["db_schema"],
                    db_stats=self.metadata["db_stats"], tau2_commit=TAU2_COMMIT, agent_trace=trace,
                    llm_usage=dict(self.llm.usage), llm_requests=getattr(self.llm, "records", []),
                    llm_config=getattr(self.llm, "params", {"model": "MockLLM"}), holdout_users=len(self.holdout))
                path = Path(out_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return result
        raise RuntimeError("Explorer turn budget exhausted without an accepted world model")
