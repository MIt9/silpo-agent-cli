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


def test_no_args_prints_help_and_exits_zero(capsys):
    assert main([]) == 0
    assert "silpo-agent" in capsys.readouterr().out


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
                    "comment": None,
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
                    "comment": None,
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
                    "comment": None,
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
                    "comment": None,
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
                    "comment": None,
                },
                {
                    "productId": "bread",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 1,
                    "addQuantity": True,
                    "comment": None,
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
                    "comment": None,
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
                    "comment": None,
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
