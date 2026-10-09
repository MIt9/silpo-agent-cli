from silpo_agent.cli import main


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def _cart(products):
    return {
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": {
            "success": True,
            "cart": {
                "deliveryType": "DeliveryHome",
                "timeslot": {"start": "2026-08-05T10:00:00", "end": "2026-08-05T12:00:00"},
                "shipments": [{"companyId": "c1", "branchId": "b1", "products": products}],
                "calculation": {"validations": []},
            },
            "loyalty": {},
        },
    }


def test_search_prints_hits_with_slugs(capsys):
    client = FakeClient(
        {
            **_cart([]),
            "silpo_find_products_batch": {
                "success": True,
                "queries": [
                    {
                        "query": "milk",
                        "totalFound": 1,
                        "products": [{"id": "p1", "name": "Milk 1L", "price": 49.9, "stock": 5, "slug": "milk-1l-1"}],
                    }
                ],
            },
        }
    )

    assert main(["search", "milk"], client=client) == 0
    out = capsys.readouterr().out
    assert "Milk 1L" in out
    assert "milk-1l-1" in out


def test_search_empty_query_errors_without_mcp_calls(capsys):
    client = FakeClient({})

    assert main(["search", "  "], client=client) == 1
    assert client.calls == []


def test_cart_clear_confirmed_empties_cart(capsys, monkeypatch):
    client = FakeClient(
        {
            **_cart([{"productId": "p1", "name": "Milk", "slug": "milk-1"}]),
            "silpo_clear_shopping_cart": {"success": True},
        }
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    assert main(["cart", "clear"], client=client) == 0
    out = capsys.readouterr().out
    assert "Cleared 1 item(s)" in out
    assert ("silpo_clear_shopping_cart", {"shoppingCartId": "cart-1"}) in client.calls


def test_cart_clear_declined_leaves_cart_untouched(capsys, monkeypatch):
    client = FakeClient({**_cart([{"productId": "p1", "name": "Milk", "slug": "milk-1"}])})
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    assert main(["cart", "clear"], client=client) == 0
    assert all(call[0] != "silpo_clear_shopping_cart" for call in client.calls)


def test_cart_clear_empty_reports_without_mutation(capsys):
    client = FakeClient({**_cart([])})

    assert main(["cart", "clear", "--yes"], client=client) == 0
    out = capsys.readouterr().out
    assert "already empty" in out
    assert all(call[0] != "silpo_clear_shopping_cart" for call in client.calls)


def test_favorites_lists_all(capsys):
    client = FakeClient(
        {
            **_cart([]),
            "silpo_get_my_favorites": {
                "success": True,
                "products": [{"id": "p1", "name": "Butter", "price": 49.9, "slug": "butter-1"}],
            },
        }
    )

    assert main(["favorites"], client=client) == 0
    assert "butter-1" in capsys.readouterr().out


def test_favorites_add_resolves_slug_and_likes(capsys):
    client = FakeClient(
        {
            **_cart([]),
            "silpo_get_product_details": {
                "success": True,
                "product": {"id": "p1", "name": "Butter", "price": 49.9, "slug": "butter-1"},
            },
            "silpo_add_or_update_favorite_products": {"success": True},
        }
    )

    assert main(["favorites", "add", "butter-1"], client=client) == 0
    assert "butter-1" in capsys.readouterr().out
    assert ("silpo_add_or_update_favorite_products", {"actions": [{"productId": "p1", "toDelete": False}]}) in client.calls


def test_favorites_remove_unknown_slug_errors_without_mutation(capsys):
    client = FakeClient(
        {
            **_cart([]),
            "silpo_get_my_favorites": {"success": True, "products": []},
        }
    )

    assert main(["favorites", "remove", "milk-1"], client=client) == 1
    assert "not in your favorites" in capsys.readouterr().out
    assert all(call[0] != "silpo_add_or_update_favorite_products" for call in client.calls)


def test_orders_lists_online(capsys):
    client = FakeClient(
        {
            "silpo_get_my_online_orders": {
                "success": True,
                "orders": [{"orderId": "o1", "number": "№101", "createdAt": "2026-07-20", "amount": 500.0,
                            "products": [{"id": "milk", "removed": False}]}],
            }
        }
    )

    assert main(["orders", "--last", "1"], client=client) == 0
    assert "№101" in capsys.readouterr().out


def test_loyalty_prints_snapshot(capsys):
    client = FakeClient(
        {
            "silpo_get_loyalty_info": {"success": True, "loyalty": {"balance": {"total": 10.0}}},
            "silpo_get_my_promos": {"success": True, "promos": []},
            "silpo_get_promo_codes": {"success": True, "promoCodes": []},
            "silpo_get_my_certificates": {"success": True, "certificates": []},
        }
    )

    assert main(["loyalty"], client=client) == 0
    assert "10.00" in capsys.readouterr().out
