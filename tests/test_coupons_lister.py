from silpo_agent.coupons_lister import list_coupons


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def test_populated_coupons_format_description_dates_and_reward():
    client = FakeClient(
        {
            "silpo_get_my_coupons": {
                "success": True,
                "summary": "Found 1 coupons",
                "coupons": [
                    {
                        "id": "c1",
                        "active": True,
                        "useWay": "auto",
                        "beginDate": "2026-08-01",
                        "endDate": "2026-08-31",
                        "description": "за купівлю вершкового масла",
                        "limitText": "1 раз",
                        "warningText": None,
                        "image": "img.png",
                    }
                ],
            },
            "silpo_get_coupon_details": {
                "success": True,
                "coupon": {
                    "id": "c1",
                    "active": True,
                    "state": "active",
                    "useWay": "auto",
                    "beginDate": "2026-08-01",
                    "endDate": "2026-08-31",
                    "usedCount": 0,
                    "description": "за купівлю вершкового масла",
                    "limitText": "1 раз",
                    "warningText": None,
                    "rewardText": "-20% на наступну покупку",
                    "rewardValue": 20.0,
                    "image": "img.png",
                },
            },
        }
    )

    lines = list_coupons(client)

    assert len(lines) == 1
    formatted = lines[0].format()
    assert "за купівлю вершкового масла" in formatted
    assert "2026-08-01" in formatted and "2026-08-31" in formatted
    assert "-20% на наступну покупку" in formatted
    assert ("silpo_get_coupon_details", {"businessCouponId": "c1"}) in client.calls


def test_no_active_coupons_returns_empty_without_error():
    client = FakeClient({"silpo_get_my_coupons": {"success": True, "summary": "Found 0 coupons", "coupons": []}})

    lines = list_coupons(client)

    assert lines == []
    assert all(call[0] != "silpo_get_coupon_details" for call in client.calls)


def test_inactive_coupons_are_skipped_and_no_cart_or_product_calls_made():
    """Acceptance criteria: no cross-referencing against cart/search/promo
    tools -- only the two coupon tools should ever be called."""
    client = FakeClient(
        {
            "silpo_get_my_coupons": {
                "success": True,
                "summary": "Found 2 coupons",
                "coupons": [
                    {
                        "id": "c1",
                        "active": True,
                        "beginDate": "2026-08-01",
                        "endDate": "2026-08-31",
                        "description": "active coupon",
                    },
                    {
                        "id": "c2",
                        "active": False,
                        "beginDate": "2026-01-01",
                        "endDate": "2026-01-31",
                        "description": "expired coupon",
                    },
                ],
            },
            "silpo_get_coupon_details": {
                "success": True,
                "coupon": {"id": "c1", "rewardText": "10% off", "rewardValue": 10.0},
            },
        }
    )

    lines = list_coupons(client)

    assert len(lines) == 1
    assert lines[0].description == "active coupon"
    assert ("silpo_get_coupon_details", {"businessCouponId": "c2"}) not in client.calls
    assert {call[0] for call in client.calls} == {"silpo_get_my_coupons", "silpo_get_coupon_details"}
