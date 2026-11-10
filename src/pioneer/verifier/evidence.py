"""[BLOG] Admission uses environment evidence; [INDEPENDENT] E1-E9 protocol."""
import hashlib
import json
from dataclasses import asdict

from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage
from tau2.data_model.simulation import SimulationRun, TerminationReason
from pioneer.env import RetailSynthEnv
from pioneer.explorer.rules import check_rule
from pioneer.schemas import CheckResult, FailureCode, ExpectedOutcome, Stressor, VerifierReport
from .grounding import derive, identity_matches, instruction_text


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def task_signatures(task, meta):
    """[INDEPENDENT] Compute signatures from content, never trust supplied hashes."""
    actions = task.evaluation_criteria.actions or []
    structure = digest(dict(tools=[a.name for a in actions], stressors=sorted(s.value for s in meta.stressors),
                            outcome=meta.expected_outcome.value))
    instance = digest(dict(actions=[dict(name=a.name, arguments=a.arguments) for a in actions],
                           initial_state=task.initial_state.model_dump(mode="json") if task.initial_state else None,
                           stressors=sorted({s.value for s in meta.stressors}), expected_outcome=meta.expected_outcome.value,
                           communicate=task.evaluation_criteria.communicate_info))
    return structure, instance


def changed_entities(before, after):
    return {(collection, key) for collection in set(before) | set(after)
            for key in set(before.get(collection, {})) | set(after.get(collection, {}))
            if before.get(collection, {}).get(key) != after.get(collection, {}).get(key)}


class EvidenceEngine:
    """Sequential deterministic checks; E8 delegates conversations to native runner."""
    def __init__(self, knowledge, *, holdout=(), official_signatures=(), seen_structure=(), seen_instance=(),
                 llm=None, trials=2, seed=0, max_steps=60, episode_runner=None):
        if trials < 1:
            raise ValueError("E8 requires at least one trial")
        self.knowledge, self.holdout = knowledge, set(holdout)
        self.official_signatures = {(r["user_id"], tuple(r["write_actions"])) for r in official_signatures}
        self.seen_structure, self.seen_instance = set(seen_structure), set(seen_instance)
        self.llm, self.trials, self.seed, self.max_steps = llm, trials, seed, max_steps
        self.episode_runner = episode_runner
        self.report = None

    def evaluate(self, task, meta, *, run_e8=True):
        self.report = None
        checks, codes, causes = [], [], {}

        def add(name, passed, details, failures=()):
            checks.append(CheckResult(name=name, passed=bool(passed), details=details))
            if not passed:
                for code in failures:
                    if code not in codes:
                        codes.append(code)
                causes[name] = details

        env = RetailSynthEnv()
        env.init(task)
        tool_types = {t["name"]: t["type"] for t in env.get_metadata()["tools"]}
        criteria = task.evaluation_criteria
        try:
            env._validate_criteria(task)
            if criteria.communicate_info and "COMMUNICATE" not in criteria.reward_basis:
                raise ValueError("communicate_info must participate in the reward basis")
            if meta.expected_outcome == ExpectedOutcome.INFORM and not criteria.communicate_info:
                raise ValueError("inform tasks require a verifiable communicated answer")
            contract = True
        except ValueError as error:
            contract = False
            contract_error = str(error)
        actions = criteria.actions or [] if criteria else []
        initial = env.state()
        auth_users = identity_matches(env, instruction_text(task)[0])
        allowed, involved, messages, replay, violations = set(), set(), [], [], []
        if task.initial_state and task.initial_state.message_history:
            messages.extend(task.initial_state.message_history)
        for index, action in enumerate(actions):
            args = action.arguments
            order = env.query_data("orders", [("order_id", "eq", args.get("order_id"))])
            uid = args.get("user_id") or (order[0]["user_id"] if order else None)
            if uid:
                involved.add(uid)
                allowed.add(("users", uid))
            if order:
                allowed.add(("orders", order[0]["order_id"]))
            if len(auth_users) == 1 and uid and uid != auth_users[0]:
                violations.append(dict(step=index, rule_id="policy:one_authenticated_user", arguments=args,
                                       authenticated_user=auth_users[0], target_user=uid))
            for rule in self.knowledge.get("rules", []):
                if rule.get("level") != "state" or action.name not in rule["tools"]:
                    continue
                if rule.get("classification") not in {"confirmed", "confirmed_by_tool", "enforced_by_tool", "policy_only"}:
                    continue
                try:
                    good = check_rule(env, rule, args)
                    error = None
                except (ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
                    good, error = False, str(exc)
                if not good:
                    violations.append(dict(step=index, rule_id=rule["rule_id"], arguments=args, error=error))
            result = env.execute(action.name, args, action.requestor)
            replay.append(dict(step=index, **asdict(result)))
            call = ToolCall(id=f"reference_{index}", name=action.name, arguments=args, requestor=action.requestor)
            # Synthesized references contain assistant writes/transfer, never READs.
            messages.extend([AssistantMessage(role="assistant", tool_calls=[call]),
                             ToolMessage(role="tool", id=call.id, requestor=action.requestor,
                                         content=result.result if result.ok else result.error, error=not result.ok)])
        valid_action_kinds = all(tool_types.get(a.name) in {"write", "generic"} and a.requestor == "assistant"
                                 and (tool_types.get(a.name) != "generic" or a.name == "transfer_to_human_agents") for a in actions)
        add("E1", contract and valid_action_kinds and all(r["ok"] for r in replay),
            dict(replay=replay, contract_error=None if contract else contract_error, valid_action_kinds=valid_action_kinds), [FailureCode.TOOL_ERROR])
        add("E2", not violations, dict(violations=violations), [FailureCode.POLICY_VIOLATION])
        final = env.state()
        changed = changed_entities(initial["db"], final["db"])
        has_write = any(tool_types.get(a.name) == "write" for a in actions)
        unexpected = changed - allowed
        state_ok = (not has_write or initial["hash"] != final["hash"]) and not unexpected
        state_failures = ([FailureCode.NO_STATE_CHANGE] if has_write and initial["hash"] == final["hash"] else [])
        if unexpected:
            state_failures.append(FailureCode.UNEXPECTED_DIFF)
        add("E3", state_ok, dict(changed=sorted(changed), allowed=sorted(allowed), unexpected=sorted(unexpected)), state_failures)
        known, text = instruction_text(task)
        # Match actual native identifiers, not every number (ZIP/product IDs are valid).
        item_ids = {v["item_id"] for v in env.query_data("variants")}
        payment_ids = {pid for u in env.query_data("users") for pid in u["payment_methods"]}
        leaked = [value for value in item_ids | payment_ids if value in text]
        grounding, grounding_error = None, None
        fresh = RetailSynthEnv()
        fresh.init(task)
        try:
            grounding = derive(task, meta, fresh, tool_types) if contract else None
        except (ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
            grounding_error = str(exc)
        if grounding:
            involved.add(grounding["user_id"])
        if not has_write and criteria and criteria.communicate_info:
            state_check = next(c for c in checks if c.name == "E3")
            state_check.details["communicate_derivable"] = bool(grounding)
            if not grounding:
                state_check.passed = False
                causes["E3"] = state_check.details
                if FailureCode.ARG_NOT_DERIVABLE not in codes:
                    codes.append(FailureCode.ARG_NOT_DERIVABLE)
        e4_codes = ([FailureCode.ID_LEAK] if leaked else []) + ([FailureCode.ARG_NOT_DERIVABLE] if not grounding else [])
        add("E4", bool(grounding) and not leaked, dict(grounding=grounding, error=grounding_error, leaked_ids=leaked), e4_codes)
        rejected = []
        for request in meta.refusal_requests:
            sandbox = RetailSynthEnv()
            sandbox.init(task)
            prefix = request.get("after_actions", 0)
            if not isinstance(prefix, int) or not 0 <= prefix <= len(actions):
                rejected.append(dict(request=request, forbidden=False, error="invalid refusal action prefix"))
                continue
            setup_ok = True
            for action in actions[:prefix]:
                setup_ok = setup_ok and sandbox.execute(action.name, action.arguments, action.requestor).ok
            failed_rules = []
            for rule in self.knowledge.get("rules", []):
                if rule.get("level") == "state" and request["tool"] in rule["tools"]:
                    try:
                        compliant = check_rule(sandbox, rule, request["arguments"])
                    except (ValueError, KeyError, TypeError, IndexError, StopIteration):
                        compliant = False
                    if not compliant:
                        failed_rules.append(rule["rule_id"])
            result = sandbox.execute(request["tool"], request["arguments"])
            rejected.append(dict(request=request, forbidden=setup_ok and (not result.ok or bool(failed_rules)),
                                 execution=asdict(result), failed_rules=failed_rules))
        refusal = meta.expected_outcome in {ExpectedOutcome.REFUSE, ExpectedOutcome.PARTIAL}
        transfer = meta.expected_outcome == ExpectedOutcome.TRANSFER
        e5 = (not refusal or bool(rejected) and all(r["forbidden"] for r in rejected))
        if meta.expected_outcome == ExpectedOutcome.REFUSE:
            e5 = e5 and not has_write
        if transfer:
            capability = meta.requested_capability
            e5 = e5 and bool(capability) and capability not in tool_types and capability.replace("_", " ").casefold() in text.replace("_", " ").casefold() and any(a.name == "transfer_to_human_agents" for a in actions)
        add("E5", e5, dict(requests=rejected, expected_outcome=meta.expected_outcome.value,
                           requested_capability=meta.requested_capability), [FailureCode.REFUSAL_NOT_FORBIDDEN])
        needs_ambiguity = Stressor.UNDERSPECIFIED in meta.stressors or meta.ambiguity is not None
        ambiguity = {"applicable": needs_ambiguity}
        e6 = not needs_ambiguity
        if meta.ambiguity:
            try:
                spec = meta.ambiguity
                broad = fresh.query_data(spec["collection"], spec["initial_where"])
                narrowed = fresh.query_data(spec["collection"], spec["initial_where"] + spec["clarified_where"])
                e6 = len(broad) > 1 and len(narrowed) == 1
                ambiguity.update(initial_count=len(broad), clarified_count=len(narrowed))
            except (ValueError, KeyError, TypeError) as exc:
                e6 = False
                ambiguity["error"] = str(exc)
        add("E6", e6, ambiguity, [FailureCode.AMBIGUITY_INVALID])
        if criteria and criteria.communicate_info:
            messages.append(AssistantMessage(role="assistant", content="\n".join(criteria.communicate_info)))
        simulation = SimulationRun(id="reference", task_id=task.id, start_time="reference", end_time="reference",
            duration=0, termination_reason=TerminationReason.USER_STOP, messages=messages)
        try:
            reward = env.reward(task, simulation).reward
            add("E7", reward == 1, dict(reward=reward), [FailureCode.REWARD_MISMATCH])
        except Exception as exc:
            add("E7", False, dict(error=f"{type(exc).__name__}: {exc}"), [FailureCode.REWARD_MISMATCH])
        # Inspect known text, explicit READ requests and initialization scope too.
        involved.update(identity_matches(fresh, known))
        for user in fresh.query_data("users"):
            if user["user_id"] in text or user["email"].casefold() in text.casefold():
                involved.add(user["user_id"])
        for step in meta.read_steps:
            for expr in step.get("arguments", {}).values():
                if expr.get("source") == "text":
                    value = expr.get("value")
                    if isinstance(value, str):
                        for order in fresh.query_data("orders", [("order_id", "eq", value)]):
                            involved.add(order["user_id"])
        base = RetailSynthEnv()
        base.init()
        for collection, key in changed_entities(base.state()["db"], initial["db"]):
            if collection == "users":
                involved.add(key)
            elif collection == "orders":
                involved.add(initial["db"]["orders"].get(key, base.state()["db"]["orders"].get(key, {})).get("user_id"))
        structure, instance = task_signatures(task, meta) if criteria else ("invalid", "invalid")
        sequence = tuple(a.name for a in actions if tool_types.get(a.name) == "write")
        duplicate = instance in self.seen_instance or any((u, sequence) in self.official_signatures for u in involved)
        leaked_users = sorted(self.holdout & involved)
        add("E9", not duplicate and not leaked_users, dict(structure_signature=structure, instance_signature=instance,
            duplicate=duplicate, structure_seen=structure in self.seen_structure, holdout_users=leaked_users),
            ([FailureCode.DUPLICATE] if duplicate else []) + ([FailureCode.HOLDOUT_LEAK] if leaked_users else []))
        if all(c.passed for c in checks) and run_e8:
            before_usage = dict(self.llm.usage) if self.llm else {}
            episodes = []
            implementation = "llm_agent_gt" if actions else "llm_agent"
            for trial in range(self.trials):
                try:
                    if self.episode_runner:
                        run = self.episode_runner(task, implementation, self.seed + trial)
                    else:
                        if self.llm is None:
                            raise ValueError("E8 requires the shared LLM client")
                        from pioneer.llm.native_bridge import native_generation
                        with native_generation(self.llm):
                            run = env.run(task, dict(agent=implementation, llm_agent=self.llm.params["model"], seed=self.seed + trial,
                                max_steps=self.max_steps), dict(llm_user=self.llm.params["model"]))
                    # Independently re-score; an injected/agent-supplied reward flag is not evidence.
                    score = env.reward(task, run).reward
                    episodes.append(dict(trial=trial, agent=implementation, reward=score,
                                         termination=run.termination_reason.value,
                                         simulation=run.model_dump(mode="json")))
                except Exception as exc:
                    episodes.append(dict(trial=trial, agent=implementation, reward=0,
                                         error=f"{type(exc).__name__}: {exc}"))
                    # Do not retry gateway failures as another trial.
                    if type(exc).__name__ in {"RateLimitError", "APIConnectionError", "ServiceUnavailableError", "AuthenticationError"}:
                        break
            usage = {k: self.llm.usage[k] - before_usage[k] for k in before_usage} if self.llm else {}
            add("E8", any(e["reward"] == 1 for e in episodes), dict(trials=episodes, llm_usage=usage), [FailureCode.UNREACHABLE_E2E])
        else:
            add("E8", False, dict(blocked_by=[c.name for c in checks if not c.passed], pending=not run_e8), [])
        checks.sort(key=lambda c: c.name)
        self.report = VerifierReport(passed=all(c.passed for c in checks), checks=checks, failure_codes=codes, diagnosis=causes)
        return self.report
