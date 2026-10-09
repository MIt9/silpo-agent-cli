import pytest

from silpo_agent.favorites import FavoriteError, add_favorite, list_favorites, remove_favorite


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


class FakeContext:
    branch_id = "b1"
    company_id = "c1"
    delivery_type = "DeliveryHome"
    timeslot_start = "2026-08-05T10:00:00"
    timeslot_end = "2026-08-05T12:00:00"


def test_lists_all_favorites_not_just_discounted():
    client = FakeClient(
        {
            "silpo_get_my_favorites": {
                "success": True,
                "products": [
                    {"id": "p1", "name": "Butter", "price": 49.9, "oldPrice": 69.9, "slug": "butter-1"},
                    {"id": "p2", "name": "Milk", "price": 39.9, "oldPrice": None, "slug": "milk-1"},
                ],
            }
        }
    )

    lines = list_favorites(client, cart_context=FakeContext())

    assert len(lines) == 2
    assert "butter-1" in lines[0].format()
    assert "milk-1" in lines[1].format()


def test_add_resolves_slug_then_likes_by_product_id():
    client = FakeClient(
        {
            "silpo_get_product_details": {
                "success": True,
                "product": {"id": "p1", "name": "Butter", "price": 49.9, "slug": "butter-1"},
            },
            "silpo_add_or_update_favorite_products": {"success": True},
        }
    )

    added = add_favorite(client, FakeContext(), "butter-1")

    assert added.product_id == "p1"
    assert ("silpo_add_or_update_favorite_products", {"actions": [{"productId": "p1", "toDelete": False}]}) in client.calls


def test_add_unknown_slug_raises_without_mutation():
    client = FakeClient({"silpo_get_product_details": {"success": True}})

    with pytest.raises(FavoriteError):
        add_favorite(client, FakeContext(), "nope-1")

    assert all(call[0] != "silpo_add_or_update_favorite_products" for call in client.calls)


def test_remove_unlikes_matched_favorite_by_slug():
    client = FakeClient(
        {
            "silpo_get_my_favorites": {
                "success": True,
                "products": [{"id": "p2", "name": "Milk", "price": 39.9, "slug": "milk-1"}],
            },
            "silpo_add_or_update_favorite_products": {"success": True},
        }
    )

    removed = remove_favorite(client, FakeContext(), "milk-1")

    assert removed.product_id == "p2"
    assert ("silpo_add_or_update_favorite_products", {"actions": [{"productId": "p2", "toDelete": True}]}) in client.calls


def test_remove_slug_not_in_favorites_raises_without_mutation():
    client = FakeClient({"silpo_get_my_favorites": {"success": True, "products": []}})

    with pytest.raises(FavoriteError):
        remove_favorite(client, FakeContext(), "milk-1")

    assert all(call[0] != "silpo_add_or_update_favorite_products" for call in client.calls)
