"""[INDEPENDENT] Bounded policy compiler and query_data predicate interpreter.

[BLOG] LLM extracts policy semantics. Supported state templates have paired
environment probes; unknown templates fail explicitly rather than passing.
All entity references below are resolved from query_data, not model assertions.
"""
from pioneer.env.retail_env import _get_path, _OPS


def ref(path):
    return {"ref": path}


def query(collection, *where):
    return {"collection": collection, "where": list(where)}


def compare(left, op, right):
    return {"compare": [left, op, right]}


def rule_catalog():
    rules = []

    def add(tool, kind, text, check, pair=False):
        rules.append(dict(rule_id=f"{tool}:{kind}", tools=[tool], text=text,
                          check=check, level="state", probe_kind=kind, per_item=pair))

    for tool in ("cancel_pending_order", "modify_pending_order_address", "modify_pending_order_items", "modify_pending_order_payment"):
        add(tool, "pending", "The order must be pending; no modification or cancellation after item modification.",
            query("orders", ["order_id", "eq", ref("args.order_id")], ["status", "eq", "pending"]))
    for tool in ("exchange_delivered_order_items", "return_delivered_order_items"):
        add(tool, "delivered", "The order must be delivered; return or exchange only once.",
            query("orders", ["order_id", "eq", ref("args.order_id")], ["status", "eq", "delivered"]))
    add("cancel_pending_order", "reason", "Cancellation reason must be no longer needed or ordered by mistake.",
        compare(ref("args.reason"), "in", ["no longer needed", "ordered by mistake"]))
    for tool in ("modify_pending_order_items", "exchange_delivered_order_items"):
        add(tool, "different", "The new item must have a different product option.",
            compare(ref("args.item_id"), "ne", ref("args.new_item_id")), True)
        add(tool, "same_product", "The new item must be of the same product type.",
            query("variants", ["item_id", "eq", ref("args.new_item_id")],
                  ["product_id", "eq", ref("item.product_id")]), True)
        add(tool, "available", "The new item must be available.",
            query("variants", ["item_id", "eq", ref("args.new_item_id")], ["available", "eq", True]), True)
    for tool in ("modify_pending_order_items", "exchange_delivered_order_items", "modify_pending_order_payment"):
        add(tool, "balance", "A gift card must cover the required payment or price difference.",
            {"any": [compare(ref("payment.source"), "ne", "gift_card"),
                     query("users", ["user_id", "eq", ref("order.user_id")],
                           ["payment_methods.{payment_method_id}.balance", "ge", ref("required_amount")])]})
    add("modify_pending_order_payment", "different_payment", "The new payment method must differ from the original.",
        compare(ref("args.payment_method_id"), "ne", ref("original_payment")))
    add("modify_pending_order_payment", "single_payment", "Only one original payment can be replaced.",
        {"all": [query("orders", ["order_id", "eq", ref("args.order_id")], ["payment_history", "len_eq", 1]),
                 compare(ref("order.payment_history.0.transaction_type"), "eq", "payment")]})
    add("return_delivered_order_items", "refund", "Refund must use the original payment method or an existing gift card.",
        {"any": [compare(ref("args.payment_method_id"), "eq", ref("original_payment")),
                 compare(ref("payment.source"), "eq", "gift_card")]})
    for name, tools, text in [
        ("authenticate", [], "Authenticate by email or name and zip even if a user id is provided."),
        ("one_user", [], "Only help one authenticated user per conversation."),
        ("confirmation", [r["tools"][0] for r in rules if r["probe_kind"] in {"pending", "delivered"}] + ["modify_user_address"],
         "List action details and obtain explicit yes before database updates."),
        ("tool_protocol", [], "At most one tool call at a time; do not mix text and tool calls."),
        ("grounded_information", [], "Do not invent information or give subjective recommendations."),
        ("transfer_scope", ["transfer_to_human_agents"], "Transfer only when outside scope, then send the required transfer message."),
    ]:
        rules.append(dict(rule_id=name, tools=tools, text=text, check=None, level="conversation"))
    return rules


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
