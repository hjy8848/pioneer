"""[INDEPENDENT] Structured query_data predicate tools; no rule templates."""
from pioneer.env.retail_env import _get_path, _OPS


def ref(path):
    return {"ref": path}


def query(collection, *where):
    return {"collection": collection, "where": list(where)}


def compare(left, op, right):
    return {"compare": [left, op, right]}


def context(env, arguments, tool):
    args = dict(arguments)
    order = env.query_data("orders", [("order_id", "eq", args.get("order_id"))])
    order = order[0] if order else {}
    uid = order.get("user_id", args.get("user_id"))
    users = env.query_data("users", [("user_id", "eq", uid)])
    user = users[0] if users else {}
    payment = user.get("payment_methods", {}).get(args.get("payment_method_id"), {})
    difference = 0
    for old, new in zip(args.get("item_ids", []), args.get("new_item_ids", [])):
        old_item = next(i for i in order["items"] if i["item_id"] == old)
        new_item = env.query_data("variants", [("item_id", "eq", new)])[0]
        difference += new_item["price"] - old_item["price"]
    history = order.get("payment_history", [])
    return dict(args=args, order=order, user=user, payment=payment,
                original_payment=history[0]["payment_method_id"] if history else None,
                required_amount=history[0]["amount"] if "item_ids" not in args and history else
                (round(difference, 2) if tool == "exchange_delivered_order_items" else difference))


def evaluate_check(env, check, ctx):
    """Execute JSON predicates using query_data and its operator vocabulary; no eval."""
    def resolve(value):
        return _get_path(ctx, value["ref"]) if isinstance(value, dict) and set(value) == {"ref"} else value
    if "all" in check:
        return all(evaluate_check(env, c, ctx) for c in check["all"])
    if "any" in check:
        return any(evaluate_check(env, c, ctx) for c in check["any"])
    if "compare" in check:
        left, op, right = check["compare"]
        return _OPS[op](resolve(left), resolve(right))
    clauses = [(field.format(**ctx["args"]), op, resolve(value)) for field, op, value in check["where"]]
    return bool(env.query_data(check["collection"], clauses))


def check_rule(env, rule, arguments):
    if rule["level"] != "state":
        raise ValueError("conversation rules cannot be checked from DB state")
    ctx = context(env, arguments, rule["tools"][0])
    if rule.get("per_item"):
        old, new = arguments["item_ids"], arguments["new_item_ids"]
        if not old or len(old) != len(new):
            return False
        for item_id, new_item_id in zip(old, new):
            ctx["args"] = dict(arguments, item_id=item_id, new_item_id=new_item_id)
            ctx["item"] = next(i for i in ctx["order"]["items"] if i["item_id"] == item_id)
            if not evaluate_check(env, rule["check"], ctx):
                return False
        return True
    return evaluate_check(env, rule["check"], ctx)
