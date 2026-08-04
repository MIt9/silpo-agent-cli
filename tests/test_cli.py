from silpo_agent.cli import main
from silpo_agent.log_store import ReorderLogStore


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def test_no_args_prints_help_and_exits_zero(capsys):
    assert main([]) == 0
    assert "silpo-agent" in capsys.readouterr().out


def test_reorder_fills_cart_and_prints_report(capsys, monkeypatch, tmp_path):
    orders = [
        {"products": [{"id": "milk", "price": 45.0, "removed": False}]},
        {"products": [{"id": "milk", "price": 44.0, "removed": False}]},
    ]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "2", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Kyiv, Some St 1" in out
    assert "milk" in out
    assert "Total: 45.00" in out
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk", "quantity": 1}]},
    ) in client.calls
    # Address Resolver runs ahead of Order Aggregator, per PRD pipeline order.
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_get_my_delivery_addresses") < tool_order.index("silpo_get_my_online_orders")
    # Confirmed address is written to the Reorder Log for audit.
    assert log_store.read_history()[0]["address"] == "Kyiv, Some St 1"
    # Delivery-type/branch context is looked up from the resolved address's
    # actual coordinates, not a nonexistent address_id.
    assert ("silpo_get_available_delivery_types", {"latitude": 50.45, "longitude": 30.52}) in client.calls


def test_reorder_resolves_cart_context_between_address_resolver_and_order_aggregator(capsys, monkeypatch, tmp_path):
    """Cart Context Resolver (issue #17) runs right after Address Resolver
    and before Order Aggregator, since branch/delivery/timeslot context is a
    dependency for tools used by later tickets. Non-empty
    cart.calculation.validations must be surfaced to the user."""
    orders = [{"items": [{"product_id": "milk", "price": 45.0}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": {
                "success": True,
                "cart": {
                    "deliveryType": "DeliveryHome",
                    "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
                    "shipments": [{"companyId": "c1", "branchId": "b1", "products": []}],
                    "calculation": {
                        "validations": [
                            {"level": "error", "type": "timeslot", "message": "timeslot.not_found", "context": []},
                        ]
                    },
                },
            },
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "timeslot.not_found" in out
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_get_my_delivery_addresses") < tool_order.index("silpo_get_my_shopping_cart")
    assert tool_order.index("silpo_get_shopping_cart_by_id") < tool_order.index("silpo_get_my_online_orders")
    assert ("silpo_get_shopping_cart_by_id", {"shoppingCartId": "cart-1"}) in client.calls


def test_reorder_pipeline_runs_substitution_resolver_between_aggregator_and_cart_writer(monkeypatch, tmp_path):
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [{"product_id": "milk-oat", "price": 50.0}],
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    assert exit_code == 0
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_get_my_online_orders") < tool_order.index("silpo_check_availability")
    assert tool_order.index("silpo_check_availability") < tool_order.index("silpo_add_or_update_cart_products")
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk-oat", "quantity": 1}]},
    ) in client.calls


def test_reorder_reports_unavailable_items(capsys, monkeypatch, tmp_path):
    orders = [
        {
            "products": [
                {"id": "milk", "price": 45.0, "removed": False},
                {"id": "eggs", "price": 60.0, "removed": False},
            ]
        }
    ]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [],
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Unavailable" in out
    assert "milk" in out and "eggs" in out
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_reorder_reports_substitution_when_auto_applied(capsys, monkeypatch, tmp_path):
    """A 1-candidate auto-substitution must actually show up in the printed
    report, not just get silently added to the cart under its replacement id.
    """
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [{"product_id": "milk-oat", "price": 50.0}],
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Substituted milk -> milk-oat" in out
    assert "Unavailable" not in out
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk-oat", "quantity": 1}]},
    ) in client.calls


def test_reorder_with_insufficient_orders_errors_without_touching_cart(capsys, monkeypatch, tmp_path):
    client = FakeClient(
        {
            "silpo_get_my_online_orders": [],
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "3", "--threshold", "0.5"], client=client, log_store=log_store)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "0" in captured.err and "3" in captured.err
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_reorder_non_empty_cart_warns_and_aborts_on_decline(capsys, monkeypatch, tmp_path):
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
            "silpo_get_my_shopping_cart": {"items": [{"product_id": "leftover"}]},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    def fake_input(prompt=""):
        return "n" if "cart" in prompt.lower() else "y"

    monkeypatch.setattr("builtins.input", fake_input)

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "cart" in out.lower()
    assert "Added" not in out
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_reorder_non_empty_cart_warns_and_proceeds_on_confirm(capsys, monkeypatch, tmp_path):
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
            "silpo_get_my_shopping_cart": {"items": [{"product_id": "leftover"}]},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Added 1 item" in out
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk", "quantity": 1}]},
    ) in client.calls


def test_reorder_budget_trims_lowest_priority_items_and_reports_them(capsys, monkeypatch, tmp_path):
    orders = [
        {
            "products": [
                {"id": "milk", "price": 45.0, "removed": False},
                {"id": "bread", "price": 30.0, "removed": False},
                {"id": "chips", "price": 25.0, "removed": False},
            ]
        },
        {
            "products": [
                {"id": "milk", "price": 45.0, "removed": False},
                {"id": "bread", "price": 30.0, "removed": False},
            ]
        },
        {"products": [{"id": "milk", "price": 45.0, "removed": False}]},
    ]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(
        ["reorder", "--last", "3", "--threshold", "0.3", "--budget", "75"], client=client, log_store=log_store
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Total: 75.00" in out
    assert "chips" in out
    assert "Trimmed" in out
    assert (
        "silpo_add_or_update_cart_products",
        {
            "items": [
                {"product_id": "milk", "quantity": 1},
                {"product_id": "bread", "quantity": 1},
            ]
        },
    ) in client.calls


def test_reorder_without_budget_never_trims_and_reports_actual_total(capsys, monkeypatch, tmp_path):
    orders = [
        {
            "products": [
                {"id": "milk", "price": 45.0, "removed": False},
                {"id": "bread", "price": 30.0, "removed": False},
            ]
        }
    ]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Total: 75.00" in out
    assert "Trimmed" not in out


def test_reorder_aborts_before_search_when_address_not_resolved(capsys, monkeypatch, tmp_path):
    """Delivery context determines product availability/pricing (PRD Address
    Resolver section), so an unresolved address hard-stops the run before
    product search — same treatment as insufficient order history.
    """
    client = FakeClient({"silpo_get_my_delivery_addresses": []})
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # blank -> no new address entered

    exit_code = main(["reorder", "--last", "2", "--threshold", "1.0"], client=client, log_store=log_store)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "address" in captured.err
    assert all(call[0] != "silpo_get_my_online_orders" for call in client.calls)
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)
    assert log_store.read_history() == []


_PROMO_TOOLS = {"silpo_get_promo_equivalent", "silpo_get_available_bonuses", "silpo_update_shopping_cart"}


def test_reorder_without_optimize_flag_makes_zero_promo_related_calls(capsys, monkeypatch, tmp_path):
    """Flag-off must be a true no-op for promo optimization (CONTEXT.md's
    "Promo optimization" entry / issue #7 acceptance criteria) -- no
    promo-equivalent lookup, no bonus lookup, no cart-wide update call.
    """
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert all(call[0] not in _PROMO_TOOLS for call in client.calls)
    assert "Promo swap" not in out
    assert "bonus" not in out.lower()
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk", "quantity": 1}]},
    ) in client.calls


def test_reorder_optimize_promos_swaps_item_and_applies_bonuses(capsys, monkeypatch, tmp_path):
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_check_availability": {"available": True},
            "silpo_get_promo_equivalent": {"product_id": "milk-promo", "price": 40.0},
            "silpo_get_available_bonuses": [{"id": "bonus-1"}],
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(
        ["reorder", "--last", "1", "--threshold", "1.0", "--optimize", "promos"],
        client=client,
        log_store=log_store,
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Promo swap: milk -> milk-promo" in out
    assert "bonus-1" in out
    assert "Total: 40.00" in out
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk-promo", "quantity": 1}]},
    ) in client.calls
    assert ("silpo_update_shopping_cart", {"bonus_ids": ["bonus-1"]}) in client.calls
    # Promo Optimizer runs between Substitution Resolver and Cart Writer.
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_check_availability") < tool_order.index("silpo_get_promo_equivalent")
    assert tool_order.index("silpo_get_promo_equivalent") < tool_order.index("silpo_add_or_update_cart_products")


def test_reorder_optimize_invalid_choice_rejected(capsys, monkeypatch, tmp_path):
    client = FakeClient({})
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    try:
        main(["reorder", "--last", "1", "--threshold", "1.0", "--optimize", "bogus"], client=client, log_store=log_store)
        raised = False
    except SystemExit:
        raised = True

    assert raised
