from silpo_agent.favorites_deals import list_favorites_deals


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def full_cart(branch_id="b1", company_id="c1", delivery_type="DeliveryHome",
              timeslot_start="2026-08-05T10:00:00", timeslot_end="2026-08-05T12:00:00"):
    return {
        "id": "cart-1",
        "deliveryType": delivery_type,
        "timeslot": {"start": timeslot_start, "end": timeslot_end},
        "shipments": [{"id": "ship-1", "companyId": company_id, "branchId": branch_id, "products": []}],
        "calculation": {"validations": []},
    }


def _responses(favorites_products, cart=None):
    return {
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": {"success": True, "cart": cart or full_cart(), "loyalty": {}},
        "silpo_get_my_favorites": {
            "success": True,
            "summary": f"Found {len(favorites_products)} favorites",
            "products": favorites_products,
        },
    }


def test_filters_to_discounted_favorites_only():
    client = FakeClient(
        _responses(
            [
                {"id": "p1", "name": "Butter", "price": 49.9, "oldPrice": 69.9},
                {"id": "p2", "name": "Milk", "price": 39.9, "oldPrice": None},
                {"id": "p3", "name": "Bread", "price": 29.9, "oldPrice": 29.9},
            ]
        )
    )

    deals = list_favorites_deals(client)

    assert len(deals) == 1
    assert deals[0].name == "Butter"
    assert deals[0].price == 49.9
    assert deals[0].old_price == 69.9
    call_args = dict((tool, args) for tool, args in client.calls)["silpo_get_my_favorites"]
    assert call_args == {"branchId": "b1", "deliveryType": "DeliveryHome", "timeslotStart": "2026-08-05T10:00:00"}


def test_discounted_favorite_carries_and_prints_its_slug():
    """Issue #50: a discounted favorite is only actionable if it can be
    handed to `cart edit --replace`, which takes slugs."""
    client = FakeClient(
        _responses(
            [
                {
                    "id": "p1",
                    "slug": "maslo-ferma-solodkovershkove-82-5-576830",
                    "name": "Butter",
                    "price": 49.9,
                    "oldPrice": 69.9,
                }
            ]
        )
    )

    deals = list_favorites_deals(client)

    assert deals[0].slug == "maslo-ferma-solodkovershkove-82-5-576830"
    assert "maslo-ferma-solodkovershkove-82-5-576830" in deals[0].format()


def test_favorite_without_a_slug_formats_without_a_dangling_identifier():
    client = FakeClient(_responses([{"id": "p1", "name": "Butter", "price": 49.9, "oldPrice": 69.9}]))

    deals = list_favorites_deals(client)

    assert deals[0].slug is None
    assert "None" not in deals[0].format()


def test_no_discounted_favorites_returns_empty_without_error():
    client = FakeClient(
        _responses(
            [
                {"id": "p1", "name": "Butter", "price": 49.9, "oldPrice": None},
                {"id": "p2", "name": "Milk", "price": 39.9, "oldPrice": 39.9},
            ]
        )
    )

    deals = list_favorites_deals(client)

    assert deals == []


def test_empty_favorites_list_returns_empty_without_error():
    client = FakeClient(_responses([]))

    deals = list_favorites_deals(client)

    assert deals == []
