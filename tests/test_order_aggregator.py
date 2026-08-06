from silpo_agent.order_aggregator import (
    InsufficientOrderHistoryError,
    TypicalItem,
    derive_typical_items,
)

# Fixture orders in the real silpo_get_my_online_orders shape (see
# docs/mcp_schema.md's "Order history tools" section) -- newest-first, line
# items under "products" keyed by "id", each carrying companyId/branchId and
# a "removed" bool.
ORDERS = [
    {
        "products": [
            {"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"},
            {"id": "bread", "price": 30.0, "removed": False, "companyId": "c1", "branchId": "b1"},
        ]
    },
    {"products": [{"id": "milk", "price": 44.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
    {
        "products": [
            {"id": "milk", "price": 43.0, "removed": False, "companyId": "c1", "branchId": "b1"},
            {"id": "bread", "price": 29.0, "removed": False, "companyId": "c1", "branchId": "b1"},
        ]
    },
    {"products": [{"id": "bread", "price": 28.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
]


def test_item_above_threshold_is_typical_with_most_recent_price():
    result = derive_typical_items(ORDERS, last=4, threshold=0.5)

    assert TypicalItem(
        product_id="milk", frequency=0.75, last_known_price=45.0, company_id="c1", branch_id="b1"
    ) in result
    assert TypicalItem(
        product_id="bread", frequency=0.75, last_known_price=30.0, company_id="c1", branch_id="b1"
    ) in result


def test_threshold_boundary_is_inclusive():
    # "milk" and "eggs" each appear in exactly 2 of 4 orders -> frequency 0.5, threshold 0.5 -> included.
    orders = [
        {"products": [{"id": "milk", "price": 10.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
        {"products": [{"id": "eggs", "price": 5.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
        {"products": [{"id": "milk", "price": 9.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
        {"products": [{"id": "eggs", "price": 4.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
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


def test_removed_items_excluded_from_frequency():
    # milk is "removed": true in the most recent order -> shouldn't count
    # toward frequency, and its price shouldn't be used as last-known-price.
    orders = [
        {"products": [{"id": "milk", "price": 50.0, "removed": True, "companyId": "c1", "branchId": "b1"}]},
        {"products": [{"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
        {"products": [{"id": "milk", "price": 44.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
        {"products": [{"id": "milk", "price": 28.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
    ]

    result = derive_typical_items(orders, last=4, threshold=0.5)

    assert result == [
        TypicalItem(product_id="milk", frequency=0.75, last_known_price=45.0, company_id="c1", branch_id="b1")
    ]


def test_company_and_branch_id_flow_through_to_typical_item():
    result = derive_typical_items(ORDERS, last=4, threshold=0.5)

    milk = next(item for item in result if item.product_id == "milk")
    assert milk.company_id == "c1"
    assert milk.branch_id == "b1"


def test_typical_item_default_quantity_is_one():
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)

    assert item.quantity == 1.0


def test_typical_quantity_is_mean_over_orders_containing_the_item_only():
    """A product's usual quantity is the arithmetic mean of its per-order
    quantity, averaged ONLY over the orders it actually appeared in -- not
    diluted by orders where it was absent (which would systematically pull
    every quantity toward zero for anything bought less than every single
    order)."""
    orders = [
        {"products": [{"id": "olivier", "price": 60.0, "quantity": 0.4, "removed": False}]},
        {"products": [{"id": "milk", "price": 45.0, "quantity": 1, "removed": False}]},
        {"products": [{"id": "olivier", "price": 62.0, "quantity": 0.6, "removed": False}]},
        {"products": [{"id": "milk", "price": 44.0, "quantity": 2, "removed": False}]},
    ]

    result = derive_typical_items(orders, last=4, threshold=0.5)

    olivier = next(item for item in result if item.product_id == "olivier")
    milk = next(item for item in result if item.product_id == "milk")
    # olivier: (0.4 + 0.6) / 2 orders it appeared in = 0.5 -- NOT (0.4 + 0.6) / 4.
    assert olivier.quantity == 0.5
    assert milk.quantity == 1.5


def test_typical_quantity_defaults_to_one_when_order_lines_carry_no_quantity_field():
    """Real order lines always carry `quantity` (docs/mcp_schema.md), but
    fixtures/older data might not -- must not crash or silently produce 0."""
    orders = [{"products": [{"id": "bread", "price": 30.0, "removed": False}]}]

    result = derive_typical_items(orders, last=1, threshold=1.0)

    assert result[0].quantity == 1.0


def test_typical_quantity_sums_duplicate_lines_within_one_order():
    """Two separate line items for the same product within one order count
    as that order's single contribution to the mean, summed together --
    matching how frequency already treats them as one occurrence, not two."""
    orders = [
        {
            "products": [
                {"id": "eggs", "price": 5.0, "quantity": 1, "removed": False},
                {"id": "eggs", "price": 5.0, "quantity": 1, "removed": False},
            ]
        },
        {"products": [{"id": "eggs", "price": 5.0, "quantity": 3, "removed": False}]},
    ]

    result = derive_typical_items(orders, last=2, threshold=1.0)

    # (1+1) from order 1, 3 from order 2 -> mean over 2 orders = (2+3)/2 = 2.5
    assert result[0].quantity == 2.5
