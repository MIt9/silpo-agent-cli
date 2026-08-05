from silpo_agent.cart_context import CartContext
from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.cart_writer import write_cart


class FakeMCPClient:
    def __init__(self, responses: dict | None = None):
        self.calls = []
        self._responses = responses or {}

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self._responses.get(tool, {})


def empty_context(shopping_cart_id="cart-1", branch_id="b1", company_id="c1", products=None):
    return CartContext(
        shopping_cart_id=shopping_cart_id,
        branch_id=branch_id,
        company_id=company_id,
        delivery_type="DeliveryHome",
        timeslot_start=None,
        timeslot_end=None,
        validations=[],
        products=products or [],
    )


def test_adds_typical_items_and_reports_total():
    client = FakeMCPClient()
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1"),
        TypicalItem(product_id="bread", frequency=0.75, last_known_price=30.0, company_id="c1", branch_id="b1"),
    ]

    report = write_cart(client, items, empty_context())

    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                },
                {
                    "productId": "bread",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                },
            ],
        },
    ) in client.calls
    assert report.items_added == [("milk", 45.0), ("bread", 30.0)]
    assert report.total == 75.0
    assert report.trimmed == []
    assert report.aborted is False


def test_no_bare_cart_read_call_made_by_write_cart():
    """The non-empty-cart guard reads real cart contents via the
    already-resolved CartContext (#17) -- write_cart itself must not call
    `silpo_get_my_shopping_cart` (it returns only a shoppingCartId, not
    contents, so reading it directly for the guard would be wrong)."""
    client = FakeMCPClient()
    items = [TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1")]

    write_cart(client, items, empty_context())

    assert all(call[0] != "silpo_get_my_shopping_cart" for call in client.calls)


def test_empty_typical_items_makes_no_cart_call():
    client = FakeMCPClient()

    report = write_cart(client, [], empty_context())

    assert client.calls == []
    assert report.items_added == []
    assert report.total == 0.0


def test_empty_existing_cart_proceeds_without_warning():
    client = FakeMCPClient()
    items = [TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1")]
    prints: list[str] = []

    report = write_cart(
        client,
        items,
        empty_context(products=[]),
        print_fn=prints.append,
        input_fn=lambda prompt="": (_ for _ in ()).throw(AssertionError("should not prompt")),
    )

    assert prints == []
    assert report.aborted is False
    assert any(call[0] == "silpo_add_or_update_cart_products" for call in client.calls)


def test_non_empty_cart_warns_and_proceeds_on_confirmation():
    context = empty_context(
        products=[{"productId": "leftover", "companyId": "c1", "branchId": "b1", "name": "Leftover"}]
    )
    client = FakeMCPClient()
    items = [TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1")]
    prints: list[str] = []

    report = write_cart(client, items, context, print_fn=prints.append, input_fn=lambda prompt="": "y")

    assert any("cart" in message.lower() for message in prints)
    assert report.aborted is False
    assert report.items_added == [("milk", 45.0)]
    assert any(call[0] == "silpo_add_or_update_cart_products" for call in client.calls)


def test_non_empty_cart_aborts_cleanly_on_decline():
    context = empty_context(
        products=[{"productId": "leftover", "companyId": "c1", "branchId": "b1", "name": "Leftover"}]
    )
    client = FakeMCPClient()
    items = [TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1")]
    prints: list[str] = []

    report = write_cart(client, items, context, print_fn=prints.append, input_fn=lambda prompt="": "n")

    assert report.aborted is True
    assert report.items_added == []
    assert report.total == 0.0
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_items_already_in_cart_are_not_re_added():
    """A rerun of reorder must not double quantities: a typical item whose
    productId already appears in CartContext.products (left over from a
    prior confirmed run) should be skipped, not re-sent with addQuantity."""
    context = empty_context(
        products=[{"productId": "milk", "companyId": "c1", "branchId": "b1", "name": "Milk"}]
    )
    client = FakeMCPClient()
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1"),
        TypicalItem(product_id="bread", frequency=0.75, last_known_price=30.0, company_id="c1", branch_id="b1"),
    ]

    report = write_cart(client, items, context, input_fn=lambda prompt="": "y")

    assert report.items_added == [("bread", 30.0)]
    added_call = next(call for call in client.calls if call[0] == "silpo_add_or_update_cart_products")
    assert [p["productId"] for p in added_call[1]["products"]] == ["bread"]


def test_budget_trims_lowest_priority_items_to_fit():
    client = FakeMCPClient()
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1"),
        TypicalItem(product_id="bread", frequency=0.75, last_known_price=30.0, company_id="c1", branch_id="b1"),
        TypicalItem(product_id="chips", frequency=0.5, last_known_price=25.0, company_id="c1", branch_id="b1"),
    ]

    report = write_cart(client, items, empty_context(), budget=75.0)

    assert report.items_added == [("milk", 45.0), ("bread", 30.0)]
    assert report.total == 75.0
    assert report.trimmed == [("chips", 25.0)]
    added_call = next(call for call in client.calls if call[0] == "silpo_add_or_update_cart_products")
    assert [p["productId"] for p in added_call[1]["products"]] == ["milk", "bread"]


def test_budget_counts_existing_cart_total_already_spent():
    client = FakeMCPClient()
    context = CartContext(
        shopping_cart_id="cart-1",
        branch_id="b1",
        company_id="c1",
        delivery_type="DeliveryHome",
        timeslot_start=None,
        timeslot_end=None,
        validations=[],
        products=[{"productId": "existing"}],
        total_after_discounts=60.0,
    )
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=10.0, company_id="c1", branch_id="b1"),
        TypicalItem(product_id="bread", frequency=0.5, last_known_price=10.0, company_id="c1", branch_id="b1"),
    ]

    report = write_cart(client, items, context, budget=75.0, input_fn=lambda prompt="": "y")

    # 60 already in the cart + both 10s would be 80, over budget -- only
    # one 10 fits (60 + 10 = 70 <= 75), the lower-frequency one is trimmed.
    assert report.items_added == [("milk", 10.0)]
    assert report.trimmed == [("bread", 10.0)]


def test_budget_none_never_trims_and_reports_actual_total():
    client = FakeMCPClient()
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1"),
        TypicalItem(product_id="chips", frequency=0.5, last_known_price=25.0, company_id="c1", branch_id="b1"),
    ]

    report = write_cart(client, items, empty_context(), budget=None)

    assert report.items_added == [("milk", 45.0), ("chips", 25.0)]
    assert report.total == 70.0
    assert report.trimmed == []


def test_plastic_bag_items_are_filtered_out_silently():
    """Tool guidance for silpo_add_or_update_cart_products: always
    ignore/skip plastic bags. A plastic bag can legitimately show up as a
    Typical Item from order history (docs/mcp_schema.md's online-orders
    example lists "Пакет біорозкладний 3 кг" etc.) -- it must never reach
    the add call, and must not be reported as added or trimmed."""
    client = FakeMCPClient()
    items = [
        TypicalItem(
            product_id="milk", frequency=1.0, last_known_price=45.0, company_id="c1", branch_id="b1", name="Молоко"
        ),
        TypicalItem(
            product_id="bag1",
            frequency=1.0,
            last_known_price=2.0,
            company_id="c1",
            branch_id="b1",
            name="Пакет біорозкладний 3 кг",
        ),
        TypicalItem(
            product_id="bag2",
            frequency=1.0,
            last_known_price=5.0,
            company_id="c1",
            branch_id="b1",
            name="Пакет Сільпо Пакет з Пакетів 18 кг",
        ),
    ]

    report = write_cart(client, items, empty_context())

    assert report.items_added == [("milk", 45.0)]
    assert report.trimmed == []
    added_call = next(call for call in client.calls if call[0] == "silpo_add_or_update_cart_products")
    assert [p["productId"] for p in added_call[1]["products"]] == ["milk"]


def test_plastic_bag_only_typical_items_results_in_no_add_call():
    client = FakeMCPClient()
    items = [
        TypicalItem(
            product_id="bag1", frequency=1.0, last_known_price=2.0, company_id="c1", branch_id="b1", name="пакет-майка"
        ),
    ]

    report = write_cart(client, items, empty_context())

    assert report.items_added == []
    assert report.total == 0.0
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_item_without_company_or_branch_falls_back_to_cart_context():
    """A substitution/promo-resolved item that lost its companyId/branchId
    should not send null to the real add call when the cart context already
    knows the branch -- fall back to CartContext's branch_id/company_id."""
    client = FakeMCPClient()
    items = [TypicalItem(product_id="milk-oat", frequency=1.0, last_known_price=50.0)]

    write_cart(client, items, empty_context(company_id="ctx-c", branch_id="ctx-b"))

    added_call = next(call for call in client.calls if call[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["companyId"] == "ctx-c"
    assert added_call[1]["products"][0]["branchId"] == "ctx-b"
