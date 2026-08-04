from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.cart_writer import write_cart


class FakeMCPClient:
    def __init__(self, response=None):
        self.calls = []
        self._response = response if response is not None else {}

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self._response


def test_adds_typical_items_and_reports_total():
    client = FakeMCPClient()
    items = [
        TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0),
        TypicalItem(product_id="bread", frequency=0.75, last_known_price=30.0),
    ]

    report = write_cart(client, items)

    assert client.calls == [
        (
            "silpo_add_or_update_cart_products",
            {
                "items": [
                    {"product_id": "milk", "quantity": 1},
                    {"product_id": "bread", "quantity": 1},
                ]
            },
        )
    ]
    assert report.items_added == [("milk", 45.0), ("bread", 30.0)]
    assert report.total == 75.0


def test_empty_typical_items_makes_no_cart_call():
    client = FakeMCPClient()

    report = write_cart(client, [])

    assert client.calls == []
    assert report.items_added == []
    assert report.total == 0.0
