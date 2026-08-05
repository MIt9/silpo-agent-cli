"""CLI wiring tests for `cart edit` (issue #30). Module-level behavior
(swap validation, plastic-bag filtering, search) is covered by
tests/test_cart_editor.py -- these tests only exercise the interactive
flow, the `--replace` flag, and the CLI's error/no-mutation reporting.
"""

from silpo_agent.cli import main
from silpo_agent.log_store import ReorderLogStore


class FakeClient:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def _resolved_cart_context_with_products(products):
    """A resolved (real branchId/companyId, real shipments) CartContext's
    underlying tool responses, with `products` as the cart's current line
    items -- same real shape as docs/mcp_schema.md's "Cart tools" section
    (`cart.shipments[0].products`)."""
    return {
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": {
            "success": True,
            "cart": {
                "deliveryType": "DeliveryHome",
                "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
                "address": {"city": "Kyiv"},
                "shipments": [{"companyId": "c1", "branchId": "b1", "products": products}],
                "calculation": {"validations": []},
            },
            "loyalty": {"bonusAvailable": None},
        },
    }


def _batch(query, products):
    return {"success": True, "queries": [{"query": query, "totalFound": len(products), "products": products}]}


def _details(product):
    """Real `silpo_get_product_details` response shape (issue #50,
    live-verified) -- the product record sits under a `"product"` key."""
    return {"success": True, "product": product}


def test_cart_edit_interactive_happy_path_swaps_item(capsys, tmp_path):
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "oat milk", [{"id": "oat-milk", "slug": "oat-milk", "name": "Oat Milk", "price": 55.0, "companyId": "c1", "branchId": "b1"}]
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "1", "oat milk", "1", "y"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Replaced milk with oat-milk" in out
    assert (
        "silpo_remove_cart_products",
        {"shoppingCartId": "cart-1", "products": [{"productId": "milk"}]},
    ) in client.calls
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "oat-milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls


def test_cart_edit_interactive_decline_confirmation_leaves_cart_untouched(capsys, tmp_path):
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "oat milk", [{"id": "oat-milk", "slug": "oat-milk", "name": "Oat Milk", "price": 55.0}]
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "1", "oat milk", "1", "n"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Aborted" in out
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_replace_flag_swaps_with_zero_prompts(capsys, tmp_path):
    """Issue #50: both arguments are slugs, and the new one resolves through
    `silpo_get_product_details` -- never the old free-text search path."""
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 2}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_product_details": _details(
                {
                    "id": "oat-milk-uuid",
                    "slug": "oat-milk",
                    "name": "Oat Milk",
                    "price": 55.0,
                    "companyId": "c2",
                    "branchId": "b2",
                }
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def input_fn(prompt=""):
        raise AssertionError("--replace must not prompt")

    exit_code = main(
        ["cart", "edit", "--replace", "milk", "oat-milk"], client=client, log_store=log_store, input_fn=input_fn
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Replaced milk with oat-milk" in out
    assert all(call[0] != "silpo_find_products_batch" for call in client.calls)
    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    # quantity preserved from the old cart line, company/branch from the
    # resolved replacement product (not the cart context's).
    assert added_call[1]["products"][0]["productId"] == "oat-milk-uuid"
    assert added_call[1]["products"][0]["quantity"] == 2
    assert added_call[1]["products"][0]["companyId"] == "c2"
    assert added_call[1]["products"][0]["branchId"] == "b2"


def test_cart_edit_replace_old_id_not_in_cart_errors_without_mutating(capsys, tmp_path):
    products = [{"productId": "bread", "slug": "bread", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_product_details": _details(
                {"id": "oat-milk-uuid", "slug": "oat-milk", "name": "Oat Milk", "price": 55.0}
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def input_fn(prompt=""):
        raise AssertionError("--replace must not prompt")

    exit_code = main(
        ["cart", "edit", "--replace", "milk", "oat-milk"], client=client, log_store=log_store, input_fn=input_fn
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "milk" in out
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_replace_new_item_not_found_errors_without_mutating(capsys, tmp_path):
    """An unresolvable slug: `silpo_get_product_details` answers without a
    `product`, and nothing is mutated."""
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_product_details": {"success": True},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def input_fn(prompt=""):
        raise AssertionError("--replace must not prompt")

    exit_code = main(
        ["cart", "edit", "--replace", "milk", "nonexistent"], client=client, log_store=log_store, input_fn=input_fn
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "not found" in out
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_add_flag_adds_new_line_with_zero_prompts(capsys, tmp_path):
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_product_details": _details(
                {
                    "id": "oat-milk-uuid",
                    "slug": "oat-milk",
                    "name": "Oat Milk",
                    "price": 55.0,
                    "companyId": "c2",
                    "branchId": "b2",
                }
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def input_fn(prompt=""):
        raise AssertionError("--add must not prompt")

    exit_code = main(["cart", "edit", "--add", "oat-milk"], client=client, log_store=log_store, input_fn=input_fn)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Added oat-milk" in out
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["productId"] == "oat-milk-uuid"
    assert added_call[1]["products"][0]["quantity"] == 1
    assert added_call[1]["products"][0]["companyId"] == "c2"
    assert added_call[1]["products"][0]["branchId"] == "b2"


def test_cart_edit_add_flag_already_in_cart_errors_without_mutating(capsys, tmp_path):
    products = [{"productId": "oat-milk-uuid", "slug": "oat-milk", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_product_details": _details(
                {"id": "oat-milk-uuid", "slug": "oat-milk", "name": "Oat Milk", "price": 55.0}
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def input_fn(prompt=""):
        raise AssertionError("--add must not prompt")

    exit_code = main(["cart", "edit", "--add", "oat-milk"], client=client, log_store=log_store, input_fn=input_fn)

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "already" in out.lower()
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_add_flag_unresolvable_slug_errors_without_mutating(capsys, tmp_path):
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_product_details": {"success": True},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def input_fn(prompt=""):
        raise AssertionError("--add must not prompt")

    exit_code = main(
        ["cart", "edit", "--add", "nonexistent"], client=client, log_store=log_store, input_fn=input_fn
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "not found" in out
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_replace_and_add_together_is_rejected_by_argparse():
    import pytest

    with pytest.raises(SystemExit):
        main(["cart", "edit", "--replace", "a", "b", "--add", "c"], client=FakeClient({}), input_fn=lambda p="": "")


def test_cart_edit_interactive_no_search_results_errors_without_mutating(capsys, tmp_path):
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch("nonexistent", []),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "1", "nonexistent"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "no results" in out.lower()
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_interactive_filters_plastic_bags_from_candidates(capsys, tmp_path):
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "пакет",
                [
                    {"id": "bag1", "name": "Пакет біорозкладний 3 кг", "price": 2.0},
                    {"id": "oat-milk", "slug": "oat-milk", "name": "Oat Milk", "price": 55.0, "companyId": "c1", "branchId": "b1"},
                ],
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "1", "пакет", "1", "y"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    # only the non-bag candidate is ever shown/selectable -- "1" picks it,
    # not the filtered-out bag.
    assert "bag1" not in out
    assert "Replaced milk with oat-milk" in out


def test_cart_edit_interactive_promo_browse_path_swaps_item(capsys, tmp_path):
    """Issue #31: choosing the promo-browse path reuses promo_finder's
    find_promo_alternatives for the specific item being replaced, and the
    resulting swap goes through the exact same remove+add call sequence as
    the free-text path (test_cart_edit_interactive_happy_path_swaps_item)."""
    products = [
        {
            "productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1,
            "name": "Молоко", "slug": "milk-slug",
        }
    ]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_similar_products": {
                "success": True,
                "products": [
                    {
                        "id": "milk-promo", "name": "Milk Promo", "slug": "milk-promo-slug",
                        "price": 38.0, "oldPrice": 50.0, "companyId": "c1", "branchId": "b1",
                    },
                ],
            },
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "2", "1", "y"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Replaced milk-slug with milk-promo-slug" in out
    assert (
        "silpo_get_similar_products",
        {"branchId": "b1", "slug": "milk-slug", "deliveryType": "DeliveryHome", "limit": 10},
    ) in client.calls
    assert (
        "silpo_remove_cart_products",
        {"shoppingCartId": "cart-1", "products": [{"productId": "milk"}]},
    ) in client.calls
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk-promo",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls


def test_cart_edit_interactive_promo_browse_no_candidates_falls_back_to_free_text(capsys, tmp_path):
    """Issue #31 acceptance criteria: an item with no discounted similar
    products falls back gracefully to the free-text search path instead of
    erroring."""
    products = [
        {
            "productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1,
            "name": "Молоко", "slug": "milk-slug",
        }
    ]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_similar_products": {"success": True, "products": []},
            "silpo_find_products_batch": _batch(
                "oat milk", [{"id": "oat-milk", "slug": "oat-milk", "name": "Oat Milk", "price": 55.0, "companyId": "c1", "branchId": "b1"}]
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "2", "oat milk", "1", "y"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no discounted alternatives" in out.lower()
    assert "falling back" in out.lower()
    assert "Replaced milk-slug with oat-milk" in out
    assert (
        "silpo_remove_cart_products",
        {"shoppingCartId": "cart-1", "products": [{"productId": "milk"}]},
    ) in client.calls
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "oat-milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls


def test_cart_edit_interactive_promo_browse_invalid_pick_errors_without_mutating(capsys, tmp_path):
    """An out-of-range pick on the promo-alternatives list is a hard error
    (like the free-text path's invalid pick) -- distinct from the
    no-candidates-found case, which falls back instead of erroring."""
    products = [
        {
            "productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1,
            "name": "Молоко", "slug": "milk-slug",
        }
    ]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_get_similar_products": {
                "success": True,
                "products": [
                    {
                        "id": "milk-promo", "name": "Milk Promo", "slug": "milk-promo-slug",
                        "price": 38.0, "oldPrice": 50.0,
                    },
                ],
            },
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "2", "99"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 1
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_interactive_slugless_item_errors_without_mutating(capsys, tmp_path):
    """A cart line with no slug can't be addressed for replacement -- it must
    error clearly, not silently swap whichever other slug-less line matched
    first."""
    products = [
        {"productId": "no-slug-1", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"},
        {"productId": "no-slug-2", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Хліб"},
    ]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "oat milk", [{"id": "oat-milk", "slug": "oat-milk", "name": "Oat Milk", "price": 55.0}]
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "1", "oat milk", "1", "y"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "slug" in out.lower()
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_reports_the_shown_label_when_the_replacement_has_no_slug(capsys, tmp_path):
    """Free-text search results aren't guaranteed to carry a slug -- the
    confirmation line falls back to the name already shown rather than
    printing "None"."""
    products = [{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch("oat milk", [{"id": "oat-milk", "name": "Oat Milk", "price": 55.0}]),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "1", "oat milk", "1", "y"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Replaced milk with Oat Milk" in out
    assert "None" not in out


def test_cart_edit_empty_cart_reports_and_exits_zero(capsys, tmp_path):
    client = FakeClient(_resolved_cart_context_with_products([]))
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def input_fn(prompt=""):
        raise AssertionError("empty cart must not prompt")

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=input_fn)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "empty" in out.lower()
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
