from collections import Counter

import pytest

from pioneer.env import RetailSynthEnv


@pytest.fixture
def env():
    e = RetailSynthEnv()
    e.init()
    return e


def test_metadata_tools_and_types():
    meta = RetailSynthEnv().get_metadata()
    types = Counter(t["type"] for t in meta["tools"])
    assert len(meta["tools"]) == 16
    assert types == {"write": 7, "read": 7, "generic": 2}
    assert "Retail agent policy" in meta["policy"]
    assert meta["db_stats"]["order_status"]["pending"] == 423
    assert meta["db_stats"]["available_items"] == 379
    assert "properties" in meta["db_schema"]


def test_query_data_matches_raw_counts(env):
    assert len(env.query_data("orders", [("status", "eq", "pending")])) == 423
    assert len(env.query_data("orders", [("status", "eq", "delivered")])) == 373
    assert len(env.query_data("variants", [("available", "eq", True)])) == 379
    users_no_orders = env.query_data("users", [("orders", "len_eq", 0)])
    assert len(users_no_orders) == 62
    with pytest.raises(ValueError):
        env.query_data("orders", [("status", "regex", "x")])


def test_legal_write_changes_hash_and_envs_are_isolated(env):
    order = env.query_data("orders", [("status", "eq", "pending")], limit=1)[0]
    other = RetailSynthEnv()
    other_hash = other.init()
    assert other_hash == env.state(include_db=False)["hash"]

    res = env.execute(
        "cancel_pending_order",
        {"order_id": order["order_id"], "reason": "no longer needed"},
    )
    assert res.ok and res.changed_state
    assert other.state(include_db=False)["hash"] == other_hash  # other env untouched
    # a fresh init() reloads the DB from disk
    assert env.init() == other_hash


def test_illegal_write_is_reported_and_leaves_state(env):
    order = env.query_data("orders", [("status", "eq", "delivered")], limit=1)[0]
    res = env.execute(
        "cancel_pending_order",
        {"order_id": order["order_id"], "reason": "no longer needed"},
    )
    assert not res.ok
    assert "Non-pending order cannot be cancelled" in res.error
    assert not res.changed_state


def test_policy_only_gap_is_not_enforced_by_tool(env):
    """Documented in docs/AUDIT.md: after modify_pending_order_items the order is
    'pending (item modified)', the policy forbids further modification, yet the
    address tool still accepts it. The Verifier must catch this, not the tool."""
    # find a pending order with an item that has another available variant
    for order in env.query_data("orders", [("status", "eq", "pending")]):
        item = order["items"][0]
        alts = env.query_data(
            "variants",
            [("product_id", "eq", item["product_id"]),
             ("available", "eq", True),
             ("item_id", "ne", item["item_id"])],
        )
        if alts:
            break
    else:
        pytest.skip("no suitable pending order")
    pm = order["payment_history"][0]["payment_method_id"]
    if pm.startswith("gift_card"):
        pytest.skip("avoid gift-card balance edge case in this smoke test")
    r1 = env.execute(
        "modify_pending_order_items",
        {"order_id": order["order_id"], "item_ids": [item["item_id"]],
         "new_item_ids": [alts[0]["item_id"]], "payment_method_id": pm},
    )
    assert r1.ok, r1.error
    a = env.query_data("users", [("user_id", "eq", order["user_id"])])[0]["address"]
    r2 = env.execute("modify_pending_order_address", {"order_id": order["order_id"], **a})
    assert r2.ok  # tool allows it; policy does not
    r3 = env.execute(
        "cancel_pending_order", {"order_id": order["order_id"], "reason": "ordered by mistake"}
    )
    assert not r3.ok  # cancel IS enforced by the tool (status != 'pending')
