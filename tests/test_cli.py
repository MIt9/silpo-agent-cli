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
        {"items": [{"product_id": "milk", "price": 45.0}]},
        {"items": [{"product_id": "milk", "price": 44.0}]},
    ]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1"}
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


def test_reorder_pipeline_runs_substitution_resolver_between_aggregator_and_cart_writer(monkeypatch, tmp_path):
    orders = [{"items": [{"product_id": "milk", "price": 45.0}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1"}
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
    orders = [{"items": [{"product_id": "milk", "price": 45.0}, {"product_id": "eggs", "price": 60.0}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1"}
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
    orders = [{"items": [{"product_id": "milk", "price": 45.0}]}]
    client = FakeClient(
        {
            "silpo_get_my_online_orders": orders,
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1"}
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
                {"id": "a1", "is_default": True, "address": "Kyiv, Some St 1"}
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
