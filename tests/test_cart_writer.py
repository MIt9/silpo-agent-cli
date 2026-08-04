from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.cart_writer import write_cart


class FakeMCPClient:
    def __init__(self, responses: dict | None = None):
        self.calls = []
        self._responses = responses or {}

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self._responses.get(tool, {})


def test_adds_typical_items_and_reports_total():
    client = FakeMCPClient()
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0),
        TypicalItem(product_id="bread", frequency=0.75, last_known_price=30.0),
    ]

    report = write_cart(client, items)

    assert client.calls == [
        ("silpo_get_my_shopping_cart", None),
        (
            "silpo_add_or_update_cart_products",
            {
                "items": [
                    {"product_id": "milk", "quantity": 1},
                    {"product_id": "bread", "quantity": 1},
                ]
            },
        ),
    ]
    assert report.items_added == [("milk", 45.0), ("bread", 30.0)]
    assert report.total == 75.0
    assert report.trimmed == []
    assert report.aborted is False


def test_empty_typical_items_makes_no_cart_call():
    client = FakeMCPClient()

    report = write_cart(client, [])

    assert client.calls == []
    assert report.items_added == []
    assert report.total == 0.0


def test_empty_existing_cart_proceeds_without_warning():
    client = FakeMCPClient({"silpo_get_my_shopping_cart": {"items": []}})
    items = [TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)]
    prints: list[str] = []

    report = write_cart(client, items, print_fn=prints.append, input_fn=lambda prompt="": (_ for _ in ()).throw(AssertionError("should not prompt")))

    assert prints == []
    assert report.aborted is False
    assert ("silpo_add_or_update_cart_products", {"items": [{"product_id": "milk", "quantity": 1}]}) in client.calls


def test_non_empty_cart_warns_and_proceeds_on_confirmation():
    client = FakeMCPClient(
        {"silpo_get_my_shopping_cart": {"items": [{"product_id": "leftover"}]}}
    )
    items = [TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)]
    prints: list[str] = []

    report = write_cart(client, items, print_fn=prints.append, input_fn=lambda prompt="": "y")

    assert any("cart" in message.lower() for message in prints)
    assert report.aborted is False
    assert report.items_added == [("milk", 45.0)]
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk", "quantity": 1}]},
    ) in client.calls


def test_non_empty_cart_aborts_cleanly_on_decline():
    client = FakeMCPClient(
        {"silpo_get_my_shopping_cart": {"items": [{"product_id": "leftover"}]}}
    )
    items = [TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)]
    prints: list[str] = []

    report = write_cart(client, items, print_fn=prints.append, input_fn=lambda prompt="": "n")

    assert report.aborted is True
    assert report.items_added == []
    assert report.total == 0.0
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_budget_trims_lowest_priority_items_to_fit():
    client = FakeMCPClient({"silpo_get_my_shopping_cart": {"items": []}})
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0),
        TypicalItem(product_id="bread", frequency=0.75, last_known_price=30.0),
        TypicalItem(product_id="chips", frequency=0.5, last_known_price=25.0),
    ]

    report = write_cart(client, items, budget=75.0)

    assert report.items_added == [("milk", 45.0), ("bread", 30.0)]
    assert report.total == 75.0
    assert report.trimmed == [("chips", 25.0)]
    assert (
        "silpo_add_or_update_cart_products",
        {
            "items": [
                {"product_id": "milk", "quantity": 1},
                {"product_id": "bread", "quantity": 1},
            ]
        },
    ) in client.calls


def test_budget_none_never_trims_and_reports_actual_total():
    client = FakeMCPClient({"silpo_get_my_shopping_cart": {"items": []}})
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0),
        TypicalItem(product_id="chips", frequency=0.5, last_known_price=25.0),
    ]

    report = write_cart(client, items, budget=None)

    assert report.items_added == [("milk", 45.0), ("chips", 25.0)]
    assert report.total == 70.0
    assert report.trimmed == []
