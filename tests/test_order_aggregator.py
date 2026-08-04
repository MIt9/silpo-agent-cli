from silpo_agent.order_aggregator import (
    InsufficientOrderHistoryError,
    TypicalItem,
    derive_typical_items,
)

# Fixture orders assumed newest-first (see docs/mcp_schema.md assumption).
ORDERS = [
    {"items": [{"product_id": "milk", "price": 45.0}, {"product_id": "bread", "price": 30.0}]},
    {"items": [{"product_id": "milk", "price": 44.0}]},
    {"items": [{"product_id": "milk", "price": 43.0}, {"product_id": "bread", "price": 29.0}]},
    {"items": [{"product_id": "bread", "price": 28.0}]},
]


def test_item_above_threshold_is_typical_with_most_recent_price():
    result = derive_typical_items(ORDERS, last=4, threshold=0.5)

    assert TypicalItem(product_id="milk", frequency=0.75, last_known_price=45.0) in result
    assert TypicalItem(product_id="bread", frequency=0.75, last_known_price=30.0) in result


def test_threshold_boundary_is_inclusive():
    # "milk" and "eggs" each appear in exactly 2 of 4 orders -> frequency 0.5, threshold 0.5 -> included.
    orders = [
        {"items": [{"product_id": "milk", "price": 10.0}]},
        {"items": [{"product_id": "eggs", "price": 5.0}]},
        {"items": [{"product_id": "milk", "price": 9.0}]},
        {"items": [{"product_id": "eggs", "price": 4.0}]},
    ]

    result = derive_typical_items(orders, last=4, threshold=0.5)

    assert {item.product_id for item in result} == {"milk", "eggs"}


def test_item_below_threshold_is_excluded():
    result = derive_typical_items(ORDERS, last=4, threshold=0.8)

    assert result == []


def test_last_limits_to_most_recent_n_orders():
    # Only the first 2 (most recent) orders considered: milk in both, bread in neither.
    result = derive_typical_items(ORDERS, last=2, threshold=1.0)

    assert [item.product_id for item in result] == ["milk"]


def test_fewer_orders_than_last_raises_without_touching_cart():
    orders = ORDERS[:2]

    try:
        derive_typical_items(orders, last=4, threshold=0.5)
        assert False, "expected InsufficientOrderHistoryError"
    except InsufficientOrderHistoryError as exc:
        assert "2" in str(exc) and "4" in str(exc)


def test_zero_orders_raises():
    try:
        derive_typical_items([], last=3, threshold=0.5)
        assert False, "expected InsufficientOrderHistoryError"
    except InsufficientOrderHistoryError:
        pass
