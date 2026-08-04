from silpo_agent.cli import main


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


def test_reorder_fills_cart_and_prints_report(capsys):
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
        }
    )

    exit_code = main(["reorder", "--last", "2", "--threshold", "1.0"], client=client)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Kyiv, Some St 1" in out
    assert "milk" in out
    assert "Total: 45.00" in out
    assert (
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": "milk", "quantity": 1}]},
    ) in client.calls


def test_reorder_with_insufficient_orders_errors_without_touching_cart(capsys):
    client = FakeClient({"silpo_get_my_online_orders": []})

    exit_code = main(["reorder", "--last", "3", "--threshold", "0.5"], client=client)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "0" in captured.err and "3" in captured.err
    assert all(call[0] != "silpo_add_or_update_cart_products" for call in client.calls)
