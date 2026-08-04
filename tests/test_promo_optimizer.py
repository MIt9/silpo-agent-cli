from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.promo_optimizer import optimize_promos


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def test_cheaper_promo_equivalent_swaps_item():
    client = FakeClient(
        {
            "silpo_get_promo_equivalent": {"product_id": "milk-promo", "price": 40.0},
            "silpo_get_available_bonuses": [],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)

    result = optimize_promos(client, [item])

    assert result.items == [TypicalItem(product_id="milk-promo", frequency=1.0, last_known_price=40.0)]
    assert result.swaps == [("milk", "milk-promo")]
    assert ("silpo_get_promo_equivalent", {"product_id": "milk"}) in client.calls


def test_no_cheaper_promo_equivalent_leaves_item_unchanged():
    client = FakeClient(
        {
            "silpo_get_promo_equivalent": None,
            "silpo_get_available_bonuses": [],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)

    result = optimize_promos(client, [item])

    assert result.items == [item]
    assert result.swaps == []


def test_promo_equivalent_not_cheaper_leaves_item_unchanged():
    client = FakeClient(
        {
            "silpo_get_promo_equivalent": {"product_id": "milk-promo", "price": 45.0},
            "silpo_get_available_bonuses": [],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)

    result = optimize_promos(client, [item])

    assert result.items == [item]
    assert result.swaps == []


def test_available_bonuses_are_applied_to_cart():
    client = FakeClient(
        {
            "silpo_get_promo_equivalent": None,
            "silpo_get_available_bonuses": [{"id": "bonus-1"}, {"id": "promo-code-x"}],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)

    result = optimize_promos(client, [item])

    assert result.bonuses_applied == ["bonus-1", "promo-code-x"]
    assert ("silpo_update_shopping_cart", {"bonus_ids": ["bonus-1", "promo-code-x"]}) in client.calls


def test_no_available_bonuses_makes_no_update_cart_call():
    client = FakeClient(
        {
            "silpo_get_promo_equivalent": None,
            "silpo_get_available_bonuses": [],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)

    result = optimize_promos(client, [item])

    assert result.bonuses_applied == []
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)
