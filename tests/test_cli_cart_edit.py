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


def test_cart_edit_interactive_happy_path_swaps_item(capsys, tmp_path):
    products = [{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "oat milk", [{"id": "oat-milk", "name": "Oat Milk", "price": 55.0, "companyId": "c1", "branchId": "b1"}]
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "oat milk", "1", "y"])

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
                    "comment": None,
                }
            ],
        },
    ) in client.calls


def test_cart_edit_interactive_decline_confirmation_leaves_cart_untouched(capsys, tmp_path):
    products = [{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "oat milk", [{"id": "oat-milk", "name": "Oat Milk", "price": 55.0}]
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "oat milk", "1", "n"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Aborted" in out
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_replace_flag_swaps_with_zero_prompts(capsys, tmp_path):
    products = [{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 2}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "oat-milk", [{"id": "oat-milk", "name": "Oat Milk", "price": 55.0, "companyId": "c2", "branchId": "b2"}]
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
    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    # quantity preserved from the old cart line, company/branch from the
    # resolved replacement product (not the cart context's).
    assert added_call[1]["products"][0]["quantity"] == 2
    assert added_call[1]["products"][0]["companyId"] == "c2"
    assert added_call[1]["products"][0]["branchId"] == "b2"


def test_cart_edit_replace_old_id_not_in_cart_errors_without_mutating(capsys, tmp_path):
    products = [{"productId": "bread", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch("oat-milk", [{"id": "oat-milk", "name": "Oat Milk", "price": 55.0}]),
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
    products = [{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch("nonexistent", []),
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


def test_cart_edit_interactive_no_search_results_errors_without_mutating(capsys, tmp_path):
    products = [{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch("nonexistent", []),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "nonexistent"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "no results" in out.lower()
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_edit_interactive_filters_plastic_bags_from_candidates(capsys, tmp_path):
    products = [{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "name": "Молоко"}]
    client = FakeClient(
        {
            **_resolved_cart_context_with_products(products),
            "silpo_find_products_batch": _batch(
                "пакет",
                [
                    {"id": "bag1", "name": "Пакет біорозкладний 3 кг", "price": 2.0},
                    {"id": "oat-milk", "name": "Oat Milk", "price": 55.0, "companyId": "c1", "branchId": "b1"},
                ],
            ),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["1", "пакет", "1", "y"])

    exit_code = main(["cart", "edit"], client=client, log_store=log_store, input_fn=lambda prompt="": next(answers))

    out = capsys.readouterr().out
    assert exit_code == 0
    # only the non-bag candidate is ever shown/selectable -- "1" picks it,
    # not the filtered-out bag.
    assert "bag1" not in out
    assert "Replaced milk with oat-milk" in out


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
