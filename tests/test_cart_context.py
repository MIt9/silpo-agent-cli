from silpo_agent.cart_context import resolve_cart_context


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def cart_response(cart, loyalty=None):
    return {"success": True, "cart": cart, "loyalty": loyalty or {}}


def full_cart(branch_id="b1", company_id="c1", delivery_type="DeliveryHome",
              timeslot_start="2026-08-04T10:00:00", timeslot_end="2026-08-04T12:00:00",
              validations=None, products=None):
    return {
        "id": "cart-1",
        "deliveryType": delivery_type,
        "timeslot": {"start": timeslot_start, "end": timeslot_end},
        "shipments": [
            {"id": "ship-1", "companyId": company_id, "branchId": branch_id, "products": products or []}
        ],
        "calculation": {"validations": validations or []},
    }


def test_resolves_full_cart_context():
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": cart_response(full_cart()),
        }
    )

    context = resolve_cart_context(client, print_fn=lambda *a: None)

    assert context.shopping_cart_id == "cart-1"
    assert context.branch_id == "b1"
    assert context.company_id == "c1"
    assert context.delivery_type == "DeliveryHome"
    assert context.timeslot_start == "2026-08-04T10:00:00"
    assert context.timeslot_end == "2026-08-04T12:00:00"
    assert context.validations == []
    assert context.products == []
    assert ("silpo_get_shopping_cart_by_id", {"shoppingCartId": "cart-1"}) in client.calls


def test_resolves_non_empty_cart_products_for_cart_writer_guard():
    products = [
        {"productId": "leftover", "companyId": "c1", "branchId": "b1", "name": "Leftover", "quantity": 1}
    ]
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": cart_response(full_cart(products=products)),
        }
    )

    context = resolve_cart_context(client, print_fn=lambda *a: None)

    assert context.products == products


def test_empty_fresh_cart_has_no_shipment_context():
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-2"},
            "silpo_get_shopping_cart_by_id": cart_response(
                {
                    "id": "cart-2",
                    "deliveryType": None,
                    "timeslot": None,
                    "shipments": [],
                    "calculation": {"validations": []},
                }
            ),
        }
    )

    context = resolve_cart_context(client, print_fn=lambda *a: None)

    assert context.shopping_cart_id == "cart-2"
    assert context.branch_id is None
    assert context.company_id is None
    assert context.delivery_type is None
    assert context.timeslot_start is None
    assert context.timeslot_end is None
    assert context.validations == []
    assert context.products == []


def test_validations_are_surfaced_via_print_fn_but_do_not_block():
    validations = [
        {"level": "error", "type": "timeslot", "message": "timeslot.not_found", "context": []},
        {"level": "error", "type": "product", "message": "product.offer.stock.max",
         "context": {"productId": "p1", "stock": 0}},
    ]
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-3"},
            "silpo_get_shopping_cart_by_id": cart_response(full_cart(validations=validations)),
        }
    )
    printed = []

    context = resolve_cart_context(client, print_fn=lambda *a: printed.append(" ".join(str(x) for x in a)))

    assert context.validations == validations
    assert any("timeslot.not_found" in line for line in printed)
    assert any("product.offer.stock.max" in line for line in printed)


def test_missing_shopping_cart_id_skips_second_call_and_returns_empty_context():
    """A malformed/unexpected `silpo_get_my_shopping_cart` response (e.g. no
    shoppingCartId at all) must not crash or send a null id to the real
    silpo_get_shopping_cart_by_id API."""
    client = FakeClient({"silpo_get_my_shopping_cart": {"items": []}})

    context = resolve_cart_context(client, print_fn=lambda *a: None)

    assert context.shopping_cart_id is None
    assert context.branch_id is None
    assert context.validations == []
    assert context.products == []
    assert all(call[0] != "silpo_get_shopping_cart_by_id" for call in client.calls)
