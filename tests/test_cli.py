from silpo_agent.cli import main
from silpo_agent.log_store import ReorderLogStore


def _available_batch(*product_ids):
    """silpo_find_products_batch response marking each product_id available
    (real shape: {"queries": [{"query", "totalFound", "products": [...]}]})."""
    return {
        "success": True,
        "queries": [
            {
                "query": product_id,
                "totalFound": 1,
                "products": [{"id": product_id, "name": product_id, "price": 1.0, "stock": 5, "available": True}],
            }
            for product_id in product_ids
        ],
    }


def _unavailable_batch(*product_ids):
    return {
        "success": True,
        "queries": [
            {
                "query": product_id,
                "totalFound": 1,
                "products": [{"id": product_id, "name": product_id, "price": 1.0, "stock": 0, "available": False}],
            }
            for product_id in product_ids
        ],
    }


def _replacements(**product_to_candidates):
    """silpo_get_replacements batch response: {"items": [{"productId",
    "replacements": [...]}, ...]}."""
    return {
        "success": True,
        "summary": f"Found replacements for {len(product_to_candidates)} products",
        "items": [
            {"productId": product_id, "replacements": candidates}
            for product_id, candidates in product_to_candidates.items()
        ],
    }


def _resolved_cart_context(bonus_available=None):
    """A resolved (non-None branchId/companyId) CartContext's underlying
    tool responses -- resolve_substitutions now skips its MCP calls entirely
    when these are unresolved (all-None), so every test exercising
    Substitution Resolver behavior needs a real cart context. `bonus_available`
    feeds the Promo Optimizer's (issue #20) bonus-application call via the
    real response's top-level "loyalty.bonusAvailable" field."""
    return {
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": {
            "success": True,
            "cart": {
                "deliveryType": "DeliveryHome",
                "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
                "address": {"city": "Kyiv"},
                "shipments": [{"companyId": "c1", "branchId": "b1", "products": []}],
                "calculation": {"validations": []},
            },
            "loyalty": {"bonusAvailable": bonus_available},
        },
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def test_no_args_shows_current_cart_same_as_cart_command(capsys):
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": {
                "success": True,
                "cart": {
                    "deliveryType": "DeliveryHome",
                    "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
                    "address": {"city": "Вінниця", "street": "Варшавська вулиця", "building": "27", "apartment": "25"},
                    "shipments": [
                        {
                            "companyId": "c1",
                            "branchId": "b1",
                            "products": [
                                {"productId": "p1", "name": "Milk 1L", "quantity": 2, "price": 49.9, "stock": 12},
                            ],
                        }
                    ],
                    "calculation": {"validations": [], "totalAfterDiscounts": 99.8},
                },
                "loyalty": {"bonusAvailable": 24.27},
            },
        }
    )

    exit_code = main([], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Delivering to: Вінниця, Варшавська вулиця, 27, кв. 25" in out
    assert "Delivery type: DeliveryHome" in out
    assert "Milk 1L" in out
    assert "Payable total: 99.80" in out
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_reorder_fills_cart_and_prints_report(capsys, monkeypatch, tmp_path):
    orders = [
        {"products": [{"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
        {"products": [{"id": "milk", "price": 44.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
    ]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_find_products_batch": _available_batch("milk"),
            **_resolved_cart_context(),
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
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls
    # Address Resolver runs ahead of Order Aggregator, per PRD pipeline order.
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_get_my_delivery_addresses") < tool_order.index("silpo_get_my_online_orders")
    # Confirmed address is written to the Reorder Log for audit.
    assert log_store.read_history()[0]["address"] == "Kyiv, Some St 1"
    # Delivery-type/branch context is looked up from the resolved address's
    # actual coordinates, not a nonexistent address_id.
    assert ("silpo_get_available_delivery_types", {"latitude": 50.45, "longitude": 30.52}) in client.calls


def test_reorder_no_shipments_cart_reuses_address_without_second_prompt(capsys, monkeypatch, tmp_path):
    """Issue #29: Cart Context Resolver's no-shipments address fallback must
    NOT prompt the user a second time in reorder's pipeline -- it already
    resolved an address itself via Address Resolver before Cart Context
    Resolver even runs. This also proves the fallback's real branch context
    reaches the final add-to-cart call (order items below carry no
    companyId/branchId of their own, so only the fallback context can supply
    branch_id -- company_id is never available from this fallback path per
    the real silpo_get_available_delivery_types response shape, so it stays
    None all the way through)."""
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_get_available_delivery_types": {
                "success": True,
                "summary": "Found 1 delivery options",
                "options": [{"deliveryType": "DeliveryHome", "branchId": "fallback-b1", "description": "Regular"}],
            },
            "silpo_find_products_batch": _available_batch("milk"),
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-2"},
            "silpo_get_shopping_cart_by_id": {
                "success": True,
                "cart": {"deliveryType": None, "timeslot": None, "shipments": [], "calculation": {"validations": []}},
                "loyalty": {},
            },
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    input_calls = []

    def fake_input(prompt=""):
        input_calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", fake_input)

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    assert exit_code == 0
    # Only ONE address-confirmation prompt across the whole run.
    assert sum(1 for prompt in input_calls if "Deliver to" in prompt) == 1
    assert client.calls.count(("silpo_get_my_delivery_addresses", None)) == 1
    # The fallback reused the delivery-types lookup silpo_get_my_delivery_addresses's
    # own call already made, rather than issuing a duplicate.
    assert client.calls.count(("silpo_get_available_delivery_types", {"latitude": 50.45, "longitude": 30.52})) == 1
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-2",
            "products": [
                {
                    "productId": "milk",
                    "companyId": None,
                    "branchId": "fallback-b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls


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
            "silpo_find_products_batch": _unavailable_batch("milk"),
            "silpo_get_replacements": _replacements(milk=[{"id": "milk-oat", "price": 50.0}]),
            **_resolved_cart_context(),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    assert exit_code == 0
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_get_my_online_orders") < tool_order.index("silpo_find_products_batch")
    assert tool_order.index("silpo_find_products_batch") < tool_order.index("silpo_add_or_update_cart_products")
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk-oat",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
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
            "silpo_find_products_batch": _unavailable_batch("milk", "eggs"),
            "silpo_get_replacements": _replacements(),
            **_resolved_cart_context(),
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
            "silpo_find_products_batch": _unavailable_batch("milk"),
            "silpo_get_replacements": _replacements(milk=[{"id": "milk-oat", "price": 50.0}]),
            **_resolved_cart_context(),
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
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk-oat",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
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
            "silpo_find_products_batch": _available_batch("milk"),
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": {
                "success": True,
                "cart": {
                    "deliveryType": "DeliveryHome",
                    "timeslot": {"start": None, "end": None},
                    "shipments": [
                        {
                            "companyId": "c1",
                            "branchId": "b1",
                            "products": [{"productId": "leftover", "companyId": "c1", "branchId": "b1"}],
                        }
                    ],
                    "calculation": {"validations": []},
                },
            },
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
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_find_products_batch": _available_batch("milk"),
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": {
                "success": True,
                "cart": {
                    "deliveryType": "DeliveryHome",
                    "timeslot": {"start": None, "end": None},
                    "shipments": [
                        {
                            "companyId": "c1",
                            "branchId": "b1",
                            "products": [{"productId": "leftover", "companyId": "c1", "branchId": "b1"}],
                        }
                    ],
                    "calculation": {"validations": []},
                },
            },
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
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls


def test_reorder_budget_trims_lowest_priority_items_and_reports_them(capsys, monkeypatch, tmp_path):
    orders = [
        {
            "products": [
                {"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"},
                {"id": "bread", "price": 30.0, "removed": False, "companyId": "c1", "branchId": "b1"},
                {"id": "chips", "price": 25.0, "removed": False, "companyId": "c1", "branchId": "b1"},
            ]
        },
        {
            "products": [
                {"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"},
                {"id": "bread", "price": 30.0, "removed": False, "companyId": "c1", "branchId": "b1"},
            ]
        },
        {"products": [{"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"}]},
    ]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_find_products_batch": _available_batch("milk", "bread", "chips"),
            **_resolved_cart_context(),
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
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                },
                {
                    "productId": "bread",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                },
            ],
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
            "silpo_find_products_batch": _available_batch("milk", "bread"),
            **_resolved_cart_context(),
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


def test_reorder_without_optimize_flag_makes_zero_promo_related_calls(capsys, monkeypatch, tmp_path):
    """Flag-off must be a true no-op for promo optimization (CONTEXT.md's
    "Promo optimization" entry / issue #20 acceptance criteria) -- no
    cart-wide update call, even when a bonus balance is available.
    """
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False, "companyId": "c1", "branchId": "b1"}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_find_products_batch": _available_batch("milk"),
            **_resolved_cart_context(bonus_available=24.27),
        }
    )
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    exit_code = main(["reorder", "--last", "1", "--threshold", "1.0"], client=client, log_store=log_store)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)
    assert "bonus" not in out.lower()
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls


def test_reorder_optimize_promos_applies_available_bonus(capsys, monkeypatch, tmp_path):
    orders = [{"products": [{"id": "milk", "price": 45.0, "removed": False}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1", "latitude": 50.45, "longitude": 30.52}
            ],
            "silpo_find_products_batch": _available_batch("milk"),
            "silpo_update_shopping_cart": {"success": True},
            **_resolved_cart_context(bonus_available=24.27),
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
    assert "Applied 24.27 bonus points to cart" in out
    assert "Total: 45.00" in out
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls
    assert (
        "silpo_update_shopping_cart",
        {
            "shoppingCartId": "cart-1",
            "deliveryType": "DeliveryHome",
            "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
            "address": {"city": "Kyiv"},
            "shipments": [{"companyId": "c1", "branchId": "b1", "products": []}],
            "bonusRequested": 24.27,
            "promoCode": None,
        },
    ) in client.calls
    # Promo Optimizer runs between Substitution Resolver and Cart Writer.
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_find_products_batch") < tool_order.index("silpo_update_shopping_cart")
    assert tool_order.index("silpo_update_shopping_cart") < tool_order.index("silpo_add_or_update_cart_products")


def test_reorder_optimize_invalid_choice_rejected(capsys, monkeypatch, tmp_path):
    client = FakeClient({})
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    try:
        main(["reorder", "--last", "1", "--threshold", "1.0", "--optimize", "bogus"], client=client, log_store=log_store)
        raised = False
    except SystemExit:
        raised = True

    assert raised


def test_clear_context_confirm_wipes_runs_and_substitutions(capsys, tmp_path):
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    log_store.append_run({"timestamp": "t", "items_added": ["milk"], "substitutions": {}, "address": "A", "total": 10})
    log_store.set_substitution("milk-1l", "milk-1l-oat")

    exit_code = main(["clear-context"], log_store=log_store, input_fn=lambda prompt="": "y", print_fn=lambda *a: None)

    assert exit_code == 0
    assert log_store.read_history() == []
    assert log_store.get_substitution("milk-1l") is None


def test_clear_context_decline_leaves_data_untouched(tmp_path):
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    run = {"timestamp": "t", "items_added": ["milk"], "substitutions": {}, "address": "A", "total": 10}
    log_store.append_run(run)
    log_store.set_substitution("milk-1l", "milk-1l-oat")

    exit_code = main(["clear-context"], log_store=log_store, input_fn=lambda prompt="": "n", print_fn=lambda *a: None)

    assert exit_code == 0
    assert log_store.read_history() == [run]
    assert log_store.get_substitution("milk-1l") == "milk-1l-oat"


def test_clear_context_on_empty_store_does_not_error(tmp_path):
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    exit_code = main(["clear-context"], log_store=log_store, input_fn=lambda prompt="": "y", print_fn=lambda *a: None)

    assert exit_code == 0
    assert log_store.read_history() == []


def test_clear_context_makes_no_mcp_calls(tmp_path):
    client = FakeClient({})
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")

    main(["clear-context"], client=client, log_store=log_store, input_fn=lambda prompt="": "y", print_fn=lambda *a: None)

    assert client.calls == []


def test_coupons_command_lists_active_coupons(capsys):
    client = FakeClient(
        {
            "silpo_get_my_coupons": {
                "success": True,
                "summary": "Found 1 coupons",
                "coupons": [
                    {
                        "id": "c1",
                        "active": True,
                        "beginDate": "2026-08-01",
                        "endDate": "2026-08-31",
                        "description": "за купівлю вершкового масла",
                    }
                ],
            },
            "silpo_get_coupon_details": {
                "success": True,
                "coupon": {"id": "c1", "rewardText": "-20% на наступну покупку", "rewardValue": 20.0},
            },
        }
    )

    exit_code = main(["coupons"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "за купівлю вершкового масла" in out
    assert "2026-08-01" in out and "2026-08-31" in out
    assert "-20% на наступну покупку" in out


def test_coupons_command_with_no_active_coupons_prints_clean_message(capsys):
    client = FakeClient({"silpo_get_my_coupons": {"success": True, "summary": "Found 0 coupons", "coupons": []}})

    exit_code = main(["coupons"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no active coupons" in out.lower()
    assert all(call[0] != "silpo_get_coupon_details" for call in client.calls)


def _favorites_cart_response(branch_id="b1", delivery_type="DeliveryHome", timeslot_start="2026-08-05T10:00:00"):
    return {
        "success": True,
        "cart": {
            "id": "cart-1",
            "deliveryType": delivery_type,
            "timeslot": {"start": timeslot_start, "end": "2026-08-05T12:00:00"},
            "shipments": [{"id": "ship-1", "companyId": "c1", "branchId": branch_id, "products": []}],
            "calculation": {"validations": []},
        },
        "loyalty": {},
    }


def test_favorites_deals_command_lists_discounted_favorites(capsys):
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": _favorites_cart_response(),
            "silpo_get_my_favorites": {
                "success": True,
                "summary": "Found 2 favorites",
                "products": [
                    {"id": "p1", "name": "Butter", "price": 49.9, "oldPrice": 69.9},
                    {"id": "p2", "name": "Milk", "price": 39.9, "oldPrice": None},
                ],
            },
        }
    )

    exit_code = main(["favorites-deals"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Butter" in out
    assert "49.90" in out and "69.90" in out
    assert "Milk" not in out
    assert ("silpo_get_my_favorites", {"branchId": "b1", "deliveryType": "DeliveryHome", "timeslotStart": "2026-08-05T10:00:00"}) in client.calls


def test_favorites_deals_command_with_no_discounts_prints_clean_message(capsys):
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": _favorites_cart_response(),
            "silpo_get_my_favorites": {"success": True, "summary": "Found 0 favorites", "products": []},
        }
    )

    exit_code = main(["favorites-deals"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no discounted favorites" in out.lower()


def test_cart_command_shows_items_payable_total_and_bonus(capsys):
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": {
                "success": True,
                "cart": {
                    "deliveryType": "DeliveryHome",
                    "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
                    "shipments": [
                        {
                            "companyId": "c1",
                            "branchId": "b1",
                            "products": [
                                {"productId": "p1", "name": "Milk 1L", "quantity": 2, "price": 49.9, "stock": 12},
                            ],
                        }
                    ],
                    "calculation": {"validations": [], "totalAfterDiscounts": 99.8},
                },
                "loyalty": {"bonusAvailable": 24.27},
            },
        }
    )

    exit_code = main(["cart"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Milk 1L" in out
    assert "Payable total: 99.80" in out
    assert "Bonus available: 24.27" in out
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)


def test_cart_command_with_empty_cart_prints_sensible_message_not_crash(capsys):
    client = FakeClient({"silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": None}})

    exit_code = main(["cart"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "empty" in out.lower()
    assert all(call[0] != "silpo_get_shopping_cart_by_id" for call in client.calls)


def test_cart_command_shows_validations_non_blocking(capsys):
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": {
                "success": True,
                "cart": {
                    "deliveryType": "DeliveryHome",
                    "timeslot": {"start": None, "end": None},
                    "shipments": [
                        {
                            "companyId": "c1",
                            "branchId": "b1",
                            "products": [
                                {"productId": "p1", "name": "Milk 1L", "quantity": 1, "price": 49.9, "stock": 0},
                            ],
                        }
                    ],
                    "calculation": {
                        "validations": [
                            {"level": "error", "type": "timeslot", "message": "timeslot.not_found", "context": []},
                        ],
                        "totalAfterDiscounts": 49.9,
                    },
                },
                "loyalty": {},
            },
        }
    )

    exit_code = main(["cart"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "timeslot.not_found" in out
    assert "Milk 1L" in out


def _delivery_fixture_responses():
    """Full fixture set for a happy-path `delivery` run: saved address,
    cart context (address/shipments template), delivery-type options
    (real shape live-verified 2026-08-05: {"options": [...]}, not
    "deliveryTypes"), and one available timeslot."""
    return {
        "silpo_get_my_delivery_addresses": [
            {"id": "a1", "city": "Вінниця", "street": "Варшавська вулиця", "building": "27",
             "apartment": None, "floor": None, "entrance": None, "latitude": 49.24, "longitude": 28.48,
             "comment": None},
        ],
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": {
            "success": True,
            "cart": {
                "deliveryType": "DeliveryHome",
                "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
                "address": {"addressType": "flat", "latitude": "49.24", "longitude": "28.48", "city": "Вінниця"},
                "shipments": [{"id": "ship-1", "companyId": "c1", "branchId": "old-branch", "products": []}],
                "calculation": {"validations": []},
            },
            "loyalty": {},
        },
        "silpo_get_available_delivery_types": {
            "success": True,
            "summary": "Found 1 delivery options",
            "options": [{"deliveryType": "DeliveryHome", "branchId": "new-branch", "description": "Regular delivery"}],
        },
        "silpo_get_time_slots": {
            "success": True,
            "summary": "Found 1 slots",
            "slots": [{"start": "2026-08-06T10:00:00", "end": "2026-08-06T12:00:00", "available": True}],
            "meta": {"total": 1},
        },
        "silpo_update_shopping_cart": {"success": True},
    }


def _deals_fixture_responses():
    """Resolved cart context (existing cart, real shipments -- no address
    fallback needed) plus one promotion category with a mix of discounted
    and non-discounted products."""
    return {
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": {
            "success": True,
            "cart": {
                "deliveryType": "DeliveryHome",
                "timeslot": {"start": "2026-08-05T10:00:00", "end": "2026-08-05T12:00:00"},
                "shipments": [{"companyId": "c1", "branchId": "b1", "products": []}],
                "calculation": {"validations": []},
            },
            "loyalty": {},
        },
        "silpo_get_promotions": {
            "success": True,
            "summary": "Found 1 promotions",
            "promotions": [{"code": "dairy", "title": "Dairy deals", "productCount": 3, "url": "/promo/dairy"}],
        },
        "silpo_get_products": {
            "success": True,
            "products": [
                {"id": "milk", "slug": "moloko-ferma-2-5-576829", "name": "Молоко", "price": 60.0, "oldPrice": 100.0, "companyId": "c1", "branchId": "b1"},
                {"id": "bread", "slug": "khlib-kyivskyi-500g", "name": "Хліб", "price": 45.0, "oldPrice": 50.0, "companyId": "c1", "branchId": "b1"},
                {"id": "bag1", "name": "Пакет-майка", "price": 1.0, "oldPrice": 3.0, "companyId": "c1", "branchId": "b1"},
            ],
        },
    }


def test_deals_command_lists_top_discounts_sorted_and_filters_bags(capsys):
    client = FakeClient(_deals_fixture_responses())

    exit_code = main(["deals"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.index("Молоко") < out.index("Хліб")
    assert "Пакет-майка" not in out
    assert ("silpo_get_promotions", {"branchId": "b1", "deliveryType": "DeliveryHome", "timeslotStart": "2026-08-05T10:00:00", "timeslotEnd": "2026-08-05T12:00:00"}) in client.calls


def test_deals_lines_carry_the_slug_so_they_can_be_fed_to_cart_edit(capsys):
    """Issue #50: a deal is only actionable if it can be handed straight to
    `cart edit --replace`, which takes slugs."""
    client = FakeClient(_deals_fixture_responses())

    main(["deals"], client=client)

    out = capsys.readouterr().out
    assert "moloko-ferma-2-5-576829" in out
    assert "khlib-kyivskyi-500g" in out


def test_deals_limit_flag_controls_result_count(capsys):
    client = FakeClient(_deals_fixture_responses())

    exit_code = main(["deals", "--limit", "1"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Молоко" in out
    assert "Хліб" not in out


def test_deals_default_limit_is_ten(capsys):
    responses = _deals_fixture_responses()
    responses["silpo_get_products"] = {
        "success": True,
        "products": [
            {"id": f"p{i}", "name": f"Product {i}", "price": 100.0 - i, "oldPrice": 100.0, "companyId": "c1", "branchId": "b1"}
            for i in range(15)
        ],
    }
    client = FakeClient(responses)

    exit_code = main(["deals"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert sum(1 for i in range(15) if f"Product {i}:" in out) == 10


def test_deals_command_with_no_active_promotions_prints_clean_message(capsys):
    responses = _deals_fixture_responses()
    responses["silpo_get_promotions"] = {"success": True, "summary": "Found 0 promotions", "promotions": []}
    client = FakeClient(responses)

    exit_code = main(["deals"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no" in out.lower() and "deal" in out.lower()
    assert all(call[0] != "silpo_get_products" for call in client.calls)


def test_deals_category_flag_filters_to_the_matched_category(capsys):
    responses = _deals_fixture_responses()
    responses["silpo_get_categories"] = {
        "success": True,
        "categories": [{"id": "id-1", "slug": "ovochi-4808", "title": "Овочі"}],
        "meta": {"total": 1},
    }
    responses["silpo_get_products"] = {
        "success": True,
        "products": [{"id": "carrot", "slug": "morkva-789561", "name": "Морква", "price": 41.42, "oldPrice": 49.9, "companyId": "c1", "branchId": "b1"}],
    }
    client = FakeClient(responses)

    exit_code = main(["deals", "--category", "Овочі"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Морква" in out
    assert all(call[0] != "silpo_get_promotions" for call in client.calls)
    products_call = next(c for c in client.calls if c[0] == "silpo_get_products")
    assert products_call[1]["category"] == "ovochi-4808"


def test_deals_category_flag_unmatched_errors_clearly(capsys):
    responses = _deals_fixture_responses()
    responses["silpo_get_categories"] = {
        "success": True,
        "categories": [{"id": "id-1", "slug": "frukty-4791", "title": "Фрукти"}],
        "meta": {"total": 1},
    }
    client = FakeClient(responses)

    exit_code = main(["deals", "--category", "Неіснуюча категорія"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Неіснуюча категорія" in out
    assert all(call[0] != "silpo_get_products" for call in client.calls)


def test_delivery_wires_end_to_end_and_exits_zero(capsys, tmp_path):
    client = FakeClient(_delivery_fixture_responses())
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["y", "1", "1"])

    exit_code = main(
        ["delivery"], client=client, log_store=log_store,
        input_fn=lambda prompt="": next(answers), print_fn=lambda *a: None,
    )

    assert exit_code == 0
    assert any(call[0] == "silpo_update_shopping_cart" for call in client.calls)


def test_delivery_invalid_selection_exits_nonzero_without_update_call(tmp_path):
    client = FakeClient(_delivery_fixture_responses())
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    answers = iter(["y", "99"])  # 99 is out of range for the single delivery-type option

    exit_code = main(
        ["delivery"], client=client, log_store=log_store,
        input_fn=lambda prompt="": next(answers), print_fn=lambda *a: None,
    )

    assert exit_code == 1


def _cart_with_products(*products):
    """Resolved-cart-context fixture (issue #28) with real cart items, each
    carrying its own `slug` per docs/mcp_schema.md's "Cart tools" section
    (`cart.shipments[0].products[].slug`)."""
    return {
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": {
            "success": True,
            "cart": {
                "deliveryType": "DeliveryHome",
                "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
                "address": {"city": "Kyiv"},
                "shipments": [{"companyId": "c1", "branchId": "b1", "products": list(products)}],
                "calculation": {"validations": []},
            },
            "loyalty": {"bonusAvailable": None},
        },
    }


class _CartPromosClient(FakeClient):
    """Like FakeClient, but silpo_get_similar_products responses vary by the
    requested slug -- the shared FakeClient only keys by tool name, which
    can't distinguish per-item promo lookups in the same run."""

    def __init__(self, responses, similar_by_slug):
        super().__init__(responses)
        self.similar_by_slug = similar_by_slug

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        if tool == "silpo_get_similar_products":
            return self.similar_by_slug.get(args["slug"], {"success": True, "products": []})
        return self.responses.get(tool)


_MUTATING_TOOLS = {
    "silpo_add_or_update_cart_products",
    "silpo_remove_cart_products",
    "silpo_clear_shopping_cart",
    "silpo_update_shopping_cart",
}


def test_cart_promos_shows_ranked_discounted_alternatives_per_item(capsys):
    client = _CartPromosClient(
        _cart_with_products(
            {
                "productId": "milk-1", "companyId": "c1", "branchId": "b1",
                "slug": "milk-2-5", "name": "Milk 2.5%", "quantity": 1, "price": 45.0,
            },
            {
                "productId": "bread-1", "companyId": "c1", "branchId": "b1",
                "slug": "bread-white", "name": "White Bread", "quantity": 1, "price": 30.0,
            },
        ),
        similar_by_slug={
            "milk-2-5": {
                "success": True,
                "products": [
                    {"id": "milk-cheap", "name": "Milk 3.2% promo", "slug": "milk-3-2", "price": 38.0, "oldPrice": 50.0},
                    {"id": "milk-bag", "name": "Пакет-майка", "slug": "bag", "price": 1.0, "oldPrice": 2.0},
                ],
            },
            "bread-white": {"success": True, "products": []},
        },
    )

    exit_code = main(["cart", "promos"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Milk 2.5%" in out
    assert "Milk 3.2% promo" in out
    assert "38.0" in out and "50.0" in out
    assert "Пакет" not in out
    assert "White Bread" in out
    assert "no discounted alternatives" in out.lower()
    assert not any(call[0] in _MUTATING_TOOLS for call in client.calls)


def test_cart_promos_prints_slugs_for_both_sides_of_a_swap(capsys):
    """Issue #50: `cart promos` is the command whose whole point is feeding
    `cart edit --replace`, so both halves of the pair must be addressable --
    the cart item's own slug (the *old* argument) and each alternative's
    slug (the *new* one)."""
    client = _CartPromosClient(
        _cart_with_products(
            {
                "productId": "milk-1", "companyId": "c1", "branchId": "b1",
                "slug": "milk-2-5", "name": "Milk 2.5%", "quantity": 1, "price": 45.0,
            },
        ),
        similar_by_slug={
            "milk-2-5": {
                "success": True,
                "products": [
                    {"id": "milk-cheap", "name": "Milk 3.2% promo", "slug": "milk-3-2", "price": 38.0, "oldPrice": 50.0},
                ],
            },
        },
    )

    main(["cart", "promos"], client=client)

    out = capsys.readouterr().out
    assert "milk-2-5" in out
    assert "milk-3-2" in out


def test_cart_promos_on_empty_cart_reports_cleanly_without_error(capsys):
    client = _CartPromosClient(_cart_with_products(), similar_by_slug={})

    exit_code = main(["cart", "promos"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "empty" in out.lower()
    assert all(call[0] != "silpo_get_similar_products" for call in client.calls)
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)


def _categories_page(*slug_titles):
    return {
        "success": True,
        "categories": [{"id": f"id-{slug}", "slug": slug, "title": title} for slug, title in slug_titles],
        "meta": {"total": len(slug_titles)},
    }


def test_deals_category_fallback_match_prints_disambiguation(capsys):
    """Issue #05: "Вино" (wine) must not silently return "Виноград" (grapes)
    deals with zero indication it wasn't an exact match -- the CLI must say
    which real category title it fell back to."""
    client = FakeClient(
        {
            "silpo_get_categories": _categories_page(("vynograd-123", "Виноград")),
            "silpo_get_products": {
                "success": True,
                "products": [{"id": "p1", "name": "Grapes", "slug": "p1", "price": 40.0, "oldPrice": 50.0}],
            },
            **_resolved_cart_context(),
        }
    )

    exit_code = main(["deals", "--category", "Вино"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert 'matched category "Виноград" for query "Вино"' in out


def test_deals_category_exact_match_prints_no_extra_output(capsys):
    """Issue #05: exact-title queries must behave exactly as before -- no
    disambiguation line."""
    client = FakeClient(
        {
            "silpo_get_categories": _categories_page(("ovochi-4808", "Овочі")),
            "silpo_get_products": {
                "success": True,
                "products": [{"id": "p1", "name": "Carrots", "slug": "p1", "price": 20.0, "oldPrice": 25.0}],
            },
            **_resolved_cart_context(),
        }
    )

    exit_code = main(["deals", "--category", "Овочі"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "matched category" not in out
    assert "Carrots" in out


def test_deals_list_categories_prints_titles_without_fetching_deals(capsys):
    """Issue #05: `deals --list-categories` is read-only -- shows what
    `--category` accepts, fetches no deals/promotions."""
    client = FakeClient(
        {
            "silpo_get_categories": _categories_page(("frukty-4791", "Фрукти"), ("ovochi-4808", "Овочі")),
            **_resolved_cart_context(),
        }
    )

    exit_code = main(["deals", "--list-categories"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Фрукти" in out
    assert "Овочі" in out
    assert all(call[0] != "silpo_get_promotions" for call in client.calls)
    assert all(call[0] != "silpo_get_products" for call in client.calls)


def test_version_flag_prints_version_and_exits_zero(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("--version should exit")

    out = capsys.readouterr().out
    assert "silpo-agent" in out


class FakeTokenStore:
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True


def test_clear_context_confirmed_also_logs_out(capsys, tmp_path):
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    log_store.append_run({"timestamp": "t", "address": "a"})
    token_store = FakeTokenStore()

    exit_code = main(["clear-context"], log_store=log_store, token_store=token_store, input_fn=lambda _: "y")

    out = capsys.readouterr().out
    assert exit_code == 0
    assert token_store.cleared is True
    assert "logged out" in out.lower()
    assert log_store.read_history() == []


def test_clear_context_declined_leaves_login_untouched(capsys, tmp_path):
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    token_store = FakeTokenStore()

    exit_code = main(["clear-context"], log_store=log_store, token_store=token_store, input_fn=lambda _: "n")

    out = capsys.readouterr().out
    assert exit_code == 0
    assert token_store.cleared is False
    assert "unchanged" in out.lower()


def test_clear_context_yes_skips_confirmation_prompt(capsys, tmp_path):
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    log_store.append_run({"timestamp": "t", "address": "a"})
    token_store = FakeTokenStore()

    def _unused_input(_):
        raise AssertionError("--yes must not prompt")

    exit_code = main(
        ["clear-context", "--yes"], log_store=log_store, token_store=token_store, input_fn=_unused_input
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert token_store.cleared is True
    assert "logged out" in out.lower()
    assert log_store.read_history() == []
