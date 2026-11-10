"""仅用于离线回归，禁止在流水线中调用。[INDEPENDENT]"""
from pioneer.explorer.rules import ref, query, compare

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


