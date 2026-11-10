"""[INDEPENDENT] Conservative READ-tool provenance checks (E4 approximation)."""
import json
import re

from pioneer.env.retail_env import _get_path, _OPS


def instruction_text(task):
    instructions = task.user_scenario.instructions
    if isinstance(instructions, str):
        return instructions, instructions
    return instructions.known_info or "", "\n".join(str(v) for v in instructions.model_dump().values() if v)


def resolve(expr, outputs, text):
    if not isinstance(expr, dict):
        raise ValueError("binding must be a text/read expression")
    if expr.get("source") == "text":
        value = expr["value"]
        leaves = value if isinstance(value, list) else [value]
        if not all(str(v).casefold() in text.casefold() for v in leaves):
            raise ValueError("literal is absent from supplied user instructions")
        return value
    if expr.get("source") != "read":
        raise ValueError("unsupported binding source")
    value = outputs[expr["step"]]
    for segment in expr.get("path", "").split("."):
        if not segment:
            continue
        if isinstance(value, list):
            if len(value) != 1:
                raise ValueError("positional access cannot disambiguate multiple candidates")
            value = value[int(segment)]
        else:
            value = value[segment]
    if "where" in expr or "pick" in expr:
        candidates = list(value.values()) if isinstance(value, dict) else list(value)
        clauses = expr.get("where", [])
        for _, _, operand in clauses:
            if isinstance(operand, dict):
                resolve(operand, outputs, text)
            elif str(operand).casefold() not in text.casefold():
                raise ValueError("selector operand is absent from the user goal")
        rows = [row for row in candidates if all(_OPS[op](_get_path(row, field),
                    resolve(operand, outputs, text) if isinstance(operand, dict) else operand)
                    for field, op, operand in clauses)]
        if "pick" in expr:
            # [INDEPENDENT] Comparison-based selection ("the cheapest"): keep the
            # row with the extreme value of one numeric field; a tie is ambiguous.
            (kind, key), = expr["pick"].items()
            if kind not in {"min", "max"}:
                raise ValueError("pick must be min or max")
            scored = [(_get_path(row, key), row) for row in rows]
            if not scored or any(not isinstance(s, (int, float)) for s, _ in scored):
                raise ValueError("pick needs a numeric field on every candidate")
            extreme = (min if kind == "min" else max)(s for s, _ in scored)
            rows = [row for s, row in scored if s == extreme]
            if len(rows) != 1:
                raise ValueError(f"pick {kind} {key} is tied between {len(rows)} candidates")
        if not expr.get("many") and len(rows) != 1:
            raise ValueError(f"selector is not unique: {len(rows)} candidates")
        values = [_get_path(row, expr["field"]) for row in rows]
        return values if expr.get("many") else values[0]
    return value


def identity_matches(env, known):
    emails = {s.casefold() for s in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", known)}
    found = []
    for user in env.query_data("users"):
        name = user["name"]["first_name"] + " " + user["name"]["last_name"]
        if user["email"].casefold() in emails or (name.casefold() in known.casefold() and
                re.search(r"(?<!\d)" + re.escape(user["address"]["zip"]) + r"(?!\d)", known)):
            found.append(user["user_id"])
    return found


def derive(task, meta, env, tool_types):
    known, text = instruction_text(task)
    identities = identity_matches(env, known)
    if len(identities) != 1:
        raise ValueError(f"known_info must identify exactly one user via email or name+zip; found {len(identities)}")
    outputs, trace = [], []
    for index, step in enumerate(meta.read_steps):
        tool = step["tool"]
        if tool_types.get(tool) != "read":
            raise ValueError("derivation steps may call READ tools only")
        args = {key: resolve(value, outputs, known if tool.startswith("find_user_id_") else text)
                for key, value in step["arguments"].items()}
        result = env.execute(tool, args)
        if not result.ok:
            raise ValueError(f"READ step {index} failed: {result.error}")
        try:
            value = json.loads(result.result)
        except json.JSONDecodeError:
            # Native tau2 returns string tool outputs verbatim (e.g. user IDs).
            value = result.result
        if tool == "get_user_details" and args.get("user_id") != identities[0]:
            raise ValueError("READ trace refers to a different authenticated user")
        if tool == "get_order_details" and value.get("user_id") != identities[0]:
            raise ValueError("READ trace refers to another user's order")
        outputs.append(value)
        trace.append(dict(step=index, tool=tool, arguments=args))
    for i, action in enumerate(task.evaluation_criteria.actions or []):
        for key, actual in action.arguments.items():
            expression = meta.argument_bindings.get(f"{i}.{key}")
            if expression is None or resolve(expression, outputs, text) != actual:
                raise ValueError(f"argument {i}.{key} is not uniquely derivable")
    communicate = task.evaluation_criteria.communicate_info or []
    if len(communicate) != len(meta.communicate_bindings):
        raise ValueError("communicate_info needs one READ-derived binding per answer")
    for expected, expr in zip(communicate, meta.communicate_bindings):
        if expr.get("source") != "read" or str(resolve(expr, outputs, text)) != expected:
            raise ValueError("communicate_info is not computed from a READ tool output")
    return dict(user_id=identities[0], read_trace=trace, communicated=communicate)
