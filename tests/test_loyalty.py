from silpo_agent.loyalty import get_loyalty_summary


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def test_loyalty_snapshot_combines_all_four_sources():
    client = FakeClient(
        {
            "silpo_get_loyalty_info": {
                "success": True,
                "loyalty": {"card": {"typeName": "Gold"}, "balance": {"total": 24.27}},
            },
            "silpo_get_my_promos": {
                "success": True,
                "promos": [{"promoId": "a", "selected": True}, {"promoId": "b", "selected": False}],
            },
            "silpo_get_promo_codes": {"success": True, "promoCodes": ["WELCOME10"]},
            "silpo_get_my_certificates": {
                "success": True,
                "certificates": [{"barcode": "123", "totalPrice": 200.0, "expireDate": "2026-12-31", "title": "Gift"}],
            },
        }
    )

    summary = get_loyalty_summary(client)

    assert summary.balance == 24.27
    assert summary.promos_total == 2
    assert summary.promos_selected == 1
    lines = summary.format()
    assert any("24.27" in line for line in lines)
    assert any("1/2 selected" in line for line in lines)
    assert any("WELCOME10" in line for line in lines)
    assert any("Gift" in line for line in lines)
    assert {tool for tool, _ in client.calls} == {
        "silpo_get_loyalty_info",
        "silpo_get_my_promos",
        "silpo_get_promo_codes",
        "silpo_get_my_certificates",
    }


def test_loyalty_empty_account_formats_without_crashing():
    client = FakeClient({})

    lines = get_loyalty_summary(client).format()

    assert any("Bonus balance: ?" in line for line in lines)
    assert any("none" in line for line in lines)
