from silpo_agent.order_history import list_offline_orders, list_online_orders


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


def test_online_orders_skip_removed_lines_in_count():
    client = FakeClient(
        {
            "silpo_get_my_online_orders": {
                "success": True,
                "orders": [
                    {
                        "orderId": "o1",
                        "number": "№101",
                        "createdAt": "2026-07-20",
                        "amount": 500.0,
                        "products": [
                            {"id": "milk", "price": 45.0, "removed": False},
                            {"id": "tomato", "price": 60.0, "removed": True},
                        ],
                    }
                ],
            }
        }
    )

    summaries = list_online_orders(client, limit=5)

    assert len(summaries) == 1
    assert summaries[0].product_count == 1
    assert "№101" in summaries[0].format()
    tool, args = client.calls[0]
    assert tool == "silpo_get_my_online_orders"
    assert args == {"limit": 5}


def test_online_orders_empty_returns_empty():
    client = FakeClient({"silpo_get_my_online_orders": {"success": True, "orders": []}})

    assert list_online_orders(client) == []


def test_offline_orders_use_branch_context_and_flag_reorderable():
    client = FakeClient(
        {
            "silpo_get_my_offline_orders": {
                "success": True,
                "orders": [
                    {
                        "filId": "f1",
                        "filialName": "Store 5",
                        "createdAt": "2026-07-19",
                        "sumReg": 300.0,
                        "products": [
                            {"name": "Milk", "catalogProduct": {"id": "p1"}},
                            {"name": "Unknown", "catalogProduct": None},
                        ],
                    }
                ],
            }
        }
    )

    summaries = list_offline_orders(client, FakeContext(), limit=5)

    assert len(summaries) == 1
    assert summaries[0].product_count == 2
    assert "1 reorderable" in summaries[0].format()
    tool, args = client.calls[0]
    assert tool == "silpo_get_my_offline_orders"
    assert args["branchId"] == "b1"
    assert args["limit"] == 5
