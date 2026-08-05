from silpo_agent.cart_context import CartContext
from silpo_agent.promo_scanner import scan_deals


class FakeClient:
    def __init__(self, responses: dict | None = None):
        self.calls = []
        self._responses = responses or {}

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self._responses.get(tool, {})


def _context(
    branch_id="b1",
    delivery_type="DeliveryHome",
    timeslot_start="2026-08-05T10:00:00",
    timeslot_end="2026-08-05T12:00:00",
):
    return CartContext(
        shopping_cart_id="cart-1",
        branch_id=branch_id,
        company_id="c1",
        delivery_type=delivery_type,
        timeslot_start=timeslot_start,
        timeslot_end=timeslot_end,
    )


def _promotions(*codes_titles):
    return {
        "success": True,
        "summary": f"Found {len(codes_titles)} promotions",
        "promotions": [
            {"code": code, "title": title, "productCount": count, "url": f"/promo/{code}"}
            for code, title, count in codes_titles
        ],
    }


def _product(product_id, price, old_price=None, name="Product"):
    return {
        "id": product_id,
        "name": name,
        "slug": product_id,
        "price": price,
        "oldPrice": old_price,
        "stock": 5,
        "available": True,
        "companyId": "c1",
        "branchId": "b1",
    }


def test_deals_carry_the_slug_so_they_can_be_fed_to_cart_edit(capsys):
    """Issue #50: a deal is only actionable if you can hand it to
    `cart edit --replace`, which takes slugs -- so the scanner must carry
    the product record's own slug through, never derive one."""
    product = _product("p1", 80.0, old_price=100.0)
    product["slug"] = "moloko-ferma-ultrapasteryzovane-2-5-576829"
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("dairy", "Dairy deals", 1)),
            "silpo_get_products": {"success": True, "products": [product]},
        }
    )

    deals = scan_deals(client, _context())

    assert deals[0].slug == "moloko-ferma-ultrapasteryzovane-2-5-576829"


def test_discount_percentage_computed_from_price_and_old_price():
    """A single promo category, single product: 100 -> 80 is a 20% discount."""
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("dairy", "Dairy deals", 1)),
            "silpo_get_products": {"success": True, "products": [_product("p1", 80.0, old_price=100.0)]},
        }
    )

    deals = scan_deals(client, _context())

    assert len(deals) == 1
    assert deals[0].product_id == "p1"
    assert deals[0].price == 80.0
    assert deals[0].old_price == 100.0
    assert deals[0].discount_pct == 20.0


def test_products_without_a_real_discount_are_excluded():
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("dairy", "Dairy deals", 2)),
            "silpo_get_products": {
                "success": True,
                "products": [
                    _product("no-old-price", 50.0, old_price=None),
                    _product("same-price", 50.0, old_price=50.0),
                ],
            },
        }
    )

    deals = scan_deals(client, _context())

    assert deals == []


def test_category_capped_to_cap_per_category_before_merge():
    """Live behavior confirmed some categories list thousands of products
    (e.g. 3517) -- the scanner must never keep more than cap_per_category
    from a single category, even if the tool response ignores the limit."""
    products = [_product(f"p{i}", price=90.0, old_price=100.0) for i in range(50)]
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("dairy", "Dairy deals", 50)),
            "silpo_get_products": {"success": True, "products": products},
        }
    )

    deals = scan_deals(client, _context(), limit=100, cap_per_category=20)

    assert len(deals) == 20


def test_category_returning_fewer_than_cap_uses_all_of_them():
    products = [_product(f"p{i}", price=90.0, old_price=100.0) for i in range(3)]
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("dairy", "Dairy deals", 3)),
            "silpo_get_products": {"success": True, "products": products},
        }
    )

    deals = scan_deals(client, _context(), cap_per_category=20)

    assert len(deals) == 3


def test_cross_category_merge_and_sort_by_discount_descending():
    client = FakeClient()

    def call(tool, args=None):
        client.calls.append((tool, args))
        if tool == "silpo_get_promotions":
            return _promotions(("dairy", "Dairy", 1), ("bakery", "Bakery", 1))
        if tool == "silpo_get_products" and args.get("promotionCode") == "dairy":
            return {"success": True, "products": [_product("milk", price=90.0, old_price=100.0, name="Milk")]}
        if tool == "silpo_get_products" and args.get("promotionCode") == "bakery":
            return {"success": True, "products": [_product("bread", price=40.0, old_price=100.0, name="Bread")]}
        return {}

    client.call = call

    deals = scan_deals(client, _context(), limit=10)

    assert [deal.product_id for deal in deals] == ["bread", "milk"]
    assert deals[0].discount_pct == 60.0
    assert deals[1].discount_pct == 10.0


def test_limit_caps_final_merged_results_to_top_n():
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("dairy", "Dairy deals", 5)),
            "silpo_get_products": {
                "success": True,
                "products": [_product(f"p{i}", price=100.0 - i, old_price=100.0) for i in range(5)],
            },
        }
    )

    deals = scan_deals(client, _context(), limit=2)

    assert len(deals) == 2
    assert [deal.product_id for deal in deals] == ["p4", "p3"]


def test_plastic_bag_products_are_filtered_out():
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("household", "Household", 2)),
            "silpo_get_products": {
                "success": True,
                "products": [
                    _product("milk", price=80.0, old_price=100.0, name="Молоко"),
                    _product("bag1", price=1.0, old_price=5.0, name="Пакет біорозкладний 3 кг"),
                ],
            },
        }
    )

    deals = scan_deals(client, _context())

    assert [deal.product_id for deal in deals] == ["milk"]


def test_no_active_promotions_returns_empty_without_product_calls():
    client = FakeClient(
        {"silpo_get_promotions": {"success": True, "summary": "Found 0 promotions", "promotions": []}}
    )

    deals = scan_deals(client, _context())

    assert deals == []
    assert all(call[0] != "silpo_get_products" for call in client.calls)


def test_get_products_called_with_cart_context_and_promotion_code():
    client = FakeClient(
        {
            "silpo_get_promotions": _promotions(("dairy", "Dairy deals", 1)),
            "silpo_get_products": {"success": True, "products": [_product("p1", 80.0, old_price=100.0)]},
        }
    )

    scan_deals(client, _context(), cap_per_category=20)

    assert (
        "silpo_get_products",
        {
            "branchId": "b1",
            "deliveryType": "DeliveryHome",
            "timeslotStart": "2026-08-05T10:00:00",
            "timeslotEnd": "2026-08-05T12:00:00",
            "mustHavePromotion": True,
            "promotionCode": "dairy",
            "limit": 20,
        },
    ) in client.calls
