from silpo_agent.cart_context import CartContext
from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.promo_optimizer import optimize_promos


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


def _context(**overrides):
    defaults = dict(
        shopping_cart_id="cart-1",
        branch_id="b1",
        company_id="c1",
        delivery_type="DeliveryHome",
        timeslot_start="2026-08-04T10:00:00",
        timeslot_end="2026-08-04T12:00:00",
        bonus_available=24.27,
        timeslot={"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
        address={"city": "Kyiv"},
        shipments=[{"id": "ship-1", "companyId": "c1", "branchId": "b1", "products": []}],
    )
    defaults.update(overrides)
    return CartContext(**defaults)


def test_available_bonus_is_applied_via_update_shopping_cart():
    client = FakeClient({"silpo_update_shopping_cart": {"success": True}})
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    context = _context()

    result = optimize_promos(client, [item], context)

    assert result.items == [item]
    assert result.bonus_applied == 24.27
    assert (
        "silpo_update_shopping_cart",
        {
            "shoppingCartId": "cart-1",
            "deliveryType": "DeliveryHome",
            "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
            "address": {"city": "Kyiv"},
            "shipments": [{"id": "ship-1", "companyId": "c1", "branchId": "b1", "products": []}],
            "bonusRequested": 24.27,
            "promoCode": None,
        },
    ) in client.calls


def test_no_bonus_available_makes_no_update_cart_call():
    client = FakeClient()
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    context = _context(bonus_available=None)

    result = optimize_promos(client, [item], context)

    assert result.items == [item]
    assert result.bonus_applied is None
    assert client.calls == []


def test_zero_bonus_available_makes_no_update_cart_call():
    client = FakeClient()
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    context = _context(bonus_available=0)

    result = optimize_promos(client, [item], context)

    assert result.bonus_applied is None
    assert client.calls == []


def test_unresolved_cart_context_makes_no_calls():
    """First-ever run / cleared cart -- resolve_cart_context returns an
    all-None CartContext (no shopping_cart_id) in that case, see
    cart_context.py. Sending None fields to silpo_update_shopping_cart would
    be wrong, so skip the call entirely."""
    client = FakeClient()
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    context = CartContext(
        shopping_cart_id=None,
        branch_id=None,
        company_id=None,
        delivery_type=None,
        timeslot_start=None,
        timeslot_end=None,
    )

    result = optimize_promos(client, [item], context)

    assert result.items == [item]
    assert result.bonus_applied is None
    assert client.calls == []


def test_missing_address_or_timeslot_makes_no_update_cart_call():
    """A resolved cart context can still legitimately lack address/timeslot
    (e.g. no shipments recorded yet, per test_cart_context.py's own
    fixtures) -- silpo_update_shopping_cart requires shipments/address/
    timeslot/deliveryType to be copied from silpo_get_shopping_cart_by_id
    as-is, so sending nulls into those required fields must be avoided,
    same guard style as substitution_resolver.py's no-cart-context skip."""
    client = FakeClient()
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    context = _context(address=None, timeslot=None)

    result = optimize_promos(client, [item], context)

    assert result.items == [item]
    assert result.bonus_applied is None
    assert client.calls == []


def test_update_shopping_cart_failure_does_not_claim_bonus_applied():
    """The mutating call's response isn't blindly trusted -- an explicit
    success=False must not be reported to the user as an applied bonus."""
    client = FakeClient({"silpo_update_shopping_cart": {"success": False}})
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    context = _context()

    result = optimize_promos(client, [item], context)

    assert result.items == [item]
    assert result.bonus_applied is None
    assert any(call[0] == "silpo_update_shopping_cart" for call in client.calls)  # the call still happened
