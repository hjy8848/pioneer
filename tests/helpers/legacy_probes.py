"""仅用于离线回归，禁止在流水线中调用。[INDEPENDENT]"""

from dataclasses import asdict

from tau2.domains.retail.data_model import CreditCard, GiftCard, OrderPayment
from pioneer.env import RetailSynthEnv
from pioneer.explorer.rules import check_rule


ITEM_TOOLS = {"modify_pending_order_items", "exchange_delivered_order_items"}


def make_case(rule, violates, holdout):
    env = RetailSynthEnv()
    env.init()
    tool, kind = rule["tools"][0], rule["probe_kind"]
    status = "delivered" if tool in {"exchange_delivered_order_items", "return_delivered_order_items"} else "pending"
    selected = None
    for record in env.query_data("orders", [("status", "eq", status), ("user_id", "not_in", sorted(holdout))]):
        if not record["items"]:
            continue
        if tool in ITEM_TOOLS:
            for item in record["items"]:
                variants = env.query_data("variants", [("product_id", "eq", item["product_id"]),
                                                       ("available", "eq", True)])
                old_available = any(v["item_id"] == item["item_id"] for v in variants)
                alts = [v for v in variants if v["item_id"] != item["item_id"]]
                if old_available and alts:
                    selected = record, item, alts[0]
                    break
            if selected:
                break
        else:
            selected = record, record["items"][0], None
            break
    if selected is None:
        raise ValueError(f"no non-holdout fixture for {rule['rule_id']}")
    record, item, alternative = selected
    order = env.env.tools.db.orders[record["order_id"]]
    user = env.env.tools.db.users[record["user_id"]]
    # Explicit, reproducible synthetic payment setup isolates unrelated constraints.
    user.payment_methods["probe_original"] = CreditCard(id="probe_original", source="credit_card", brand="visa", last_four="0001")
    user.payment_methods["probe_payment"] = CreditCard(id="probe_payment", source="credit_card", brand="visa", last_four="0002")
    user.payment_methods["probe_gift"] = GiftCard(id="probe_gift", source="gift_card", balance=100000)
    order.payment_history = [OrderPayment(transaction_type="payment", amount=100, payment_method_id="probe_original")]
    args = dict(order_id=order.order_id)
    if tool == "cancel_pending_order":
        args["reason"] = "no longer needed"
    elif tool == "modify_pending_order_address":
        args.update(user.address.model_dump())
        args["address1"] = "123 Probe Street"
    elif tool in ITEM_TOOLS:
        args.update(item_ids=[item["item_id"]], new_item_ids=[alternative["item_id"]], payment_method_id="probe_payment")
    elif tool == "modify_pending_order_payment":
        args["payment_method_id"] = "probe_payment"
    elif tool == "return_delivered_order_items":
        args.update(item_ids=[item["item_id"]], payment_method_id="probe_original")
    else:
        raise ValueError(f"unsupported probe tool {tool}")
    if kind == "balance":
        args["payment_method_id"] = "probe_gift"
        if tool in ITEM_TOOLS:
            next(i for i in order.items if i.item_id == item["item_id"]).price = 0
        user.payment_methods["probe_gift"].balance = 0 if violates else 100000
    if violates:
        if kind == "pending":
            order.status = "pending (item modified)"
        elif kind == "delivered":
            order.status = "pending"
        elif kind == "reason":
            args["reason"] = "probe invalid reason"
        elif kind == "different":
            args["new_item_ids"] = args["item_ids"][:]
        elif kind == "same_product":
            args["new_item_ids"] = [env.query_data("variants", [("product_id", "ne", item["product_id"]),
                                                                    ("available", "eq", True)], limit=1)[0]["item_id"]]
        elif kind == "available":
            env.env.tools.db.products[item["product_id"]].variants[alternative["item_id"]].available = False
        elif kind == "different_payment":
            args["payment_method_id"] = "probe_original"
        elif kind == "single_payment":
            order.payment_history.append(order.payment_history[0].model_copy())
        elif kind == "refund":
            args["payment_method_id"] = "probe_payment"
    return env, args


def probe_rule(rule, holdout):
    cases = []
    for violates in (False, True):
        env, args = make_case(rule, violates, holdout)
        compliant = check_rule(env, rule, args)
        if compliant != (not violates):
            raise ValueError(f"probe/check mismatch for {rule['rule_id']}")
        order = env.query_data("orders", [("order_id", "eq", args["order_id"])])[0]
        before = dict(hash=env.state(False)["hash"], order=order,
                      user=env.query_data("users", [("user_id", "eq", order["user_id"])])[0],
                      variants=env.query_data("variants", [("item_id", "in", args.get("new_item_ids", []))]))
        result = env.execute(rule["tools"][0], args)
        cases.append(dict(violates=violates, check_passed=compliant,
                          initial_state=before, execution=asdict(result)))
    positive, negative = cases
    if not positive["execution"]["ok"]:
        classification = "contradicted"
    elif negative["execution"]["ok"]:
        classification = "policy_only"
    else:
        classification = "enforced_by_tool"
    # Failure of positive control is visible and is never promoted to a valid rule.
    return dict(classification=classification, cases=cases)
