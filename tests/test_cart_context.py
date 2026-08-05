from silpo_agent.address_resolver import ResolvedAddress
from silpo_agent.cart_context import resolve_cart_context


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


class FakeLogStore:
    def __init__(self):
        self.runs = []
        self.substitutions = {}

    def append_run(self, run):
        self.runs.append(run)

    def set_substitution(self, item_id, replacement_id):
        self.substitutions[item_id] = replacement_id

    def get_substitution(self, item_id):
        return self.substitutions.get(item_id)


def make_input(*answers):
    it = iter(answers)

    def input_fn(prompt=""):
        return next(it)

    return input_fn


def addresses_response(*addresses):
    return {"success": True, "summary": f"Found {len(addresses)} delivery addresses", "addresses": list(addresses)}


def saved_address(id, city, street, building, latitude=49.1, longitude=28.1):
    return {
        "id": id,
        "tag": None,
        "city": city,
        "street": street,
        "building": building,
        "apartment": None,
        "floor": None,
        "entrance": None,
        "latitude": latitude,
        "longitude": longitude,
        "comment": None,
    }


def cart_response(cart, loyalty=None):
    return {"success": True, "cart": cart, "loyalty": loyalty or {}}


def full_cart(branch_id="b1", company_id="c1", delivery_type="DeliveryHome",
              timeslot_start="2026-08-04T10:00:00", timeslot_end="2026-08-04T12:00:00",
              validations=None, products=None, total_after_discounts=166.7):
    # total_after_discounts defaults to a real value (issue #27) for every
    # caller of this shared fixture, not just the total_after_discounts
    # test -- confirmed safe: no existing test in this module asserts the
    # field is absent/None.
    return {
        "id": "cart-1",
        "deliveryType": delivery_type,
        "timeslot": {"start": timeslot_start, "end": timeslot_end},
        "shipments": [
            {"id": "ship-1", "companyId": company_id, "branchId": branch_id, "products": products or []}
        ],
        "calculation": {"validations": validations or [], "totalAfterDiscounts": total_after_discounts},
    }


def test_resolves_full_cart_context():
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": cart_response(full_cart(), loyalty={"bonusAvailable": 24.27}),
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
    # Raw fields needed by promo_optimizer.py's silpo_update_shopping_cart call
    # (issue #20) -- must be carried through as-is, not reconstructed.
    assert context.bonus_available == 24.27
    assert context.timeslot == {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"}
    assert context.address is None
    assert context.shipments == full_cart()["shipments"]
    # Payable total (issue #27): cart.calculation.totalAfterDiscounts, never
    # the pre-discount "total" -- CartContext previously had no total field.
    assert context.total_after_discounts == 166.7


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


def _empty_shipments_cart_client(extra_responses=None):
    return FakeClient(
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
            **(extra_responses or {}),
        }
    )


def test_empty_fresh_cart_with_no_resolvable_address_has_no_shipment_context():
    """When the address-resolver fallback itself can't resolve an address
    (no saved addresses, blank new-address entry), the cart context is left
    exactly as before -- all-None branch/delivery context, nothing crashes."""
    client = _empty_shipments_cart_client({"silpo_get_my_delivery_addresses": addresses_response()})
    log_store = FakeLogStore()

    context = resolve_cart_context(
        client, print_fn=lambda *a: None, input_fn=make_input(""), log_store=log_store
    )

    assert context.shopping_cart_id == "cart-2"
    assert context.branch_id is None
    assert context.company_id is None
    assert context.delivery_type is None
    assert context.timeslot_start is None
    assert context.timeslot_end is None
    assert context.validations == []
    assert context.products == []
    assert context.bonus_available is None
    assert context.timeslot is None
    assert context.address is None
    assert context.shipments == []


def test_cart_with_shipments_does_not_trigger_address_fallback():
    """A cart that already has real shipment/branch context must never run
    the address-resolver fallback -- not even a lookup call, let alone a
    prompt -- regardless of what's passed for resolved_address/log_store."""
    client = FakeClient(
        {
            "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
            "silpo_get_shopping_cart_by_id": cart_response(full_cart()),
        }
    )

    context = resolve_cart_context(client, print_fn=lambda *a: None)

    assert context.branch_id == "b1"
    assert context.company_id == "c1"
    assert context.delivery_type == "DeliveryHome"
    assert all(
        call[0] not in ("silpo_get_my_delivery_addresses", "silpo_find_address", "silpo_get_available_delivery_types")
        for call in client.calls
    )


def test_no_shipments_with_preresolved_address_reuses_its_delivery_types():
    """Issue #29: when the caller (e.g. reorder's pipeline) already resolved
    an address itself, resolve_cart_context must reuse its attached
    delivery_types rather than prompting again or calling
    silpo_get_available_delivery_types a second time."""
    resolved_address = ResolvedAddress(
        id="a1",
        label="Kyiv, Some St 1",
        latitude=50.45,
        longitude=30.52,
        delivery_types={
            "success": True,
            "summary": "Found 1 delivery options",
            "options": [{"deliveryType": "DeliveryHome", "branchId": "fallback-b1", "description": "Regular"}],
        },
    )
    client = _empty_shipments_cart_client()

    context = resolve_cart_context(client, print_fn=lambda *a: None, resolved_address=resolved_address)

    assert context.branch_id == "fallback-b1"
    assert context.delivery_type == "DeliveryHome"
    assert all(
        call[0] not in ("silpo_get_my_delivery_addresses", "silpo_find_address", "silpo_get_available_delivery_types")
        for call in client.calls
    )


def test_no_shipments_without_preresolved_address_runs_interactive_fallback():
    """When nobody upstream already resolved an address (a command other
    than reorder calling resolve_cart_context directly), the fallback runs
    the Address Resolver's own interactive flow itself."""
    client = _empty_shipments_cart_client(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Вінниця", "Варшавська вулиця", "27", latitude=49.233, longitude=28.468)
            ),
            "silpo_get_available_delivery_types": {
                "success": True,
                "summary": "Found 1 delivery options",
                "options": [{"deliveryType": "DeliveryHome", "branchId": "b9", "description": "Regular"}],
            },
        }
    )
    log_store = FakeLogStore()

    context = resolve_cart_context(
        client, print_fn=lambda *a: None, input_fn=make_input("y"), log_store=log_store
    )

    assert context.branch_id == "b9"
    assert context.delivery_type == "DeliveryHome"
    assert ("silpo_get_my_delivery_addresses", None) in client.calls
    assert log_store.runs and log_store.runs[0]["address_id"] == "a1"


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
