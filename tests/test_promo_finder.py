from silpo_agent.cart_context import CartContext
from silpo_agent.promo_finder import find_promo_alternatives


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def _cart_context(**overrides):
    defaults = dict(
        shopping_cart_id="cart-1",
        branch_id="b1",
        company_id="c1",
        delivery_type="DeliveryHome",
        timeslot_start=None,
        timeslot_end=None,
    )
    defaults.update(overrides)
    return CartContext(**defaults)


def test_filters_to_genuinely_discounted_candidates_only():
    client = FakeClient(
        {
            "silpo_get_similar_products": {
                "success": True,
                "summary": "Found 3 similar products",
                "products": [
                    {"id": "p1", "name": "Milk 2.5%", "slug": "milk-2-5", "price": 40.0, "oldPrice": 50.0},
                    {"id": "p2", "name": "Milk 3.2%", "slug": "milk-3-2", "price": 45.0, "oldPrice": None},
                    {"id": "p3", "name": "Milk 1%", "slug": "milk-1", "price": 30.0, "oldPrice": 30.0},
                ],
                "meta": {"total": 3},
            }
        }
    )

    candidates = find_promo_alternatives(client, _cart_context(), "milk-original")

    assert [c.product_id for c in candidates] == ["p1"]
    assert (
        "silpo_get_similar_products",
        {"branchId": "b1", "slug": "milk-original", "deliveryType": "DeliveryHome", "limit": 10},
    ) in client.calls


def test_ranks_discounted_candidates_by_discount_size_descending():
    client = FakeClient(
        {
            "silpo_get_similar_products": {
                "success": True,
                "products": [
                    {"id": "small-discount", "name": "A", "slug": "a", "price": 48.0, "oldPrice": 50.0},  # -2
                    {"id": "big-discount", "name": "B", "slug": "b", "price": 20.0, "oldPrice": 50.0},  # -30
                    {"id": "mid-discount", "name": "C", "slug": "c", "price": 30.0, "oldPrice": 50.0},  # -20
                ],
            }
        }
    )

    candidates = find_promo_alternatives(client, _cart_context(), "some-slug")

    assert [c.product_id for c in candidates] == ["big-discount", "mid-discount", "small-discount"]
    assert [c.discount for c in candidates] == [30.0, 20.0, 2.0]


def test_no_discounted_matches_returns_empty_list_not_error():
    client = FakeClient(
        {
            "silpo_get_similar_products": {
                "success": True,
                "products": [
                    {"id": "p1", "name": "A", "slug": "a", "price": 50.0, "oldPrice": None},
                    {"id": "p2", "name": "B", "slug": "b", "price": 50.0, "oldPrice": 50.0},
                    {"id": "p3", "name": "C", "slug": "c", "price": 55.0, "oldPrice": 50.0},
                ],
            }
        }
    )

    candidates = find_promo_alternatives(client, _cart_context(), "some-slug")

    assert candidates == []


def test_plastic_bag_candidates_are_filtered_out_even_when_discounted():
    client = FakeClient(
        {
            "silpo_get_similar_products": {
                "success": True,
                "products": [
                    {"id": "bag-1", "name": "Пакет-майка", "slug": "bag", "price": 1.0, "oldPrice": 2.0},
                    {"id": "bag-2", "name": "ПАКЕТ з пакетів", "slug": "bag2", "price": 3.0, "oldPrice": 5.0},
                    {"id": "real", "name": "Milk 2.5%", "slug": "milk", "price": 40.0, "oldPrice": 50.0},
                ],
            }
        }
    )

    candidates = find_promo_alternatives(client, _cart_context(), "some-slug")

    assert [c.product_id for c in candidates] == ["real"]


def test_empty_similar_products_response_returns_empty_list():
    client = FakeClient({"silpo_get_similar_products": {"success": True, "products": []}})

    candidates = find_promo_alternatives(client, _cart_context(), "some-slug")

    assert candidates == []
