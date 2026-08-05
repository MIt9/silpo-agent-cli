"""Cart Context Resolver: resolves the real cart/delivery context
(`shoppingCartId`, `branchId`, `companyId`, `deliveryType`, `timeslot`) that
almost every product-facing MCP tool requires (`silpo_get_replacements`,
`silpo_get_products`, `silpo_find_products_batch`, `silpo_get_product_details`,
`silpo_get_similar_products`, `silpo_add_or_update_cart_products`).

Real schema (see ../../docs/mcp_schema.md's "Cart tools" section, live-verified):
- `silpo_get_my_shopping_cart` takes no args and returns ONLY
  `{"success", "shoppingCartId"}` -- no cart contents.
- `silpo_get_shopping_cart_by_id({"shoppingCartId"})` returns the actual
  cart: `cart.shipments[0].branchId`/`.companyId`, `cart.deliveryType`,
  `cart.timeslot.start`/`.end`, `cart.calculation.validations[]`.

`cart.calculation.validations[]` reports real problems (out-of-stock items,
stale timeslots) -- surfaced to the user here via `print_fn`, never silently
dropped, but never blocks the run (deeper handling is out of scope for this
ticket per issue #17).

`CartContext.products` carries `cart.shipments[0].products` (each with
`productId`/`companyId`/`branchId`/`quantity`/... per the schema above) so
the Cart Writer's non-empty-cart guard (#19) can read real cart contents
without a second `silpo_get_shopping_cart_by_id` call.

Nothing else downstream consumes this module's output yet -- that wiring
lands in the Substitution Resolver fix (#18) and Promo Optimizer redesign
(#20).

Issue #29 -- no-shipments address fallback: a fresh account or a cleared
cart has `cart.shipments == []`, so `branchId`/`companyId`/`deliveryType`
would otherwise resolve to `None`, breaking every downstream MCP call that
needs real branch/delivery context (Substitution Resolver already skips its
own calls entirely in that case -- see substitution_resolver.py's
no-cart-context guard). When shipments are empty, this resolver now runs
the Address Resolver (`address_resolver.resolve_address`) to get a
confirmed address, then reads `ResolvedAddress.delivery_types` -- the raw
`silpo_get_available_delivery_types` response `resolve_address` already
fetches for that address's coordinates -- to fill in real `branchId`/
`deliveryType` (`companyId` is never available from this response -- see
`_branch_context_from_delivery_types`). `silpo_get_available_delivery_types`'s
real response shape (`{"options": [{"deliveryType", "branchId",
"description"}]}`) was live-verified in issue #37, after this ticket first
shipped with a guessed, wrong key name (`"deliveryTypes"`) -- fixed here.

Callers that already resolved an address themselves (`reorder`'s pipeline,
via `cli.py`) must pass it as `resolved_address=` so this fallback reuses it
instead of prompting the user a second time. Callers with no address of
their own (future `cart`/`deals`/etc. commands) can omit it -- the fallback
then runs `resolve_address`'s own interactive confirm/pick/new-address flow,
using `log_store`/`input_fn`/`print_fn` passed through for that purpose.
"""

from dataclasses import dataclass, field

from silpo_agent.address_resolver import resolve_address
from silpo_agent.log_store import ReorderLogStore


@dataclass(frozen=True)
class CartContext:
    shopping_cart_id: str | None
    branch_id: str | None
    company_id: str | None
    delivery_type: str | None
    timeslot_start: str | None
    timeslot_end: str | None
    validations: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    # Raw fields below exist only so promo_optimizer.py (#20) can call
    # silpo_update_shopping_cart, whose own tool description requires
    # shipments/address/timeslot/deliveryType to be copied verbatim from
    # silpo_get_shopping_cart_by_id's response, not reconstructed.
    # bonus_available mirrors the response's top-level "loyalty.bonusAvailable"
    # (same value the cart's "loyalty" node reports, per docs/mcp_schema.md).
    bonus_available: float | None = None
    timeslot: dict | None = None
    address: dict | None = None
    shipments: list[dict] = field(default_factory=list)


def _empty_context(shopping_cart_id: str | None = None) -> CartContext:
    return CartContext(
        shopping_cart_id=shopping_cart_id,
        branch_id=None,
        company_id=None,
        delivery_type=None,
        timeslot_start=None,
        timeslot_end=None,
        validations=[],
        products=[],
    )


def _branch_context_from_delivery_types(response: dict | None) -> tuple[str | None, str | None, str | None]:
    """(branch_id, company_id, delivery_type) extraction from a
    `silpo_get_available_delivery_types` response. Real, live-verified shape
    (docs/mcp_schema.md, issue #37): `{"options": [{"deliveryType",
    "branchId", "description"}]}` -- no `companyId` anywhere in this
    response, so company_id is always None from this path. Prefers a
    `DeliveryHome` option, falling back to the first option."""
    if not isinstance(response, dict):
        return None, None, None

    options = response.get("options")
    if not isinstance(options, list) or not options:
        return None, None, None

    chosen = next((o for o in options if isinstance(o, dict) and o.get("deliveryType") == "DeliveryHome"), options[0])
    if not isinstance(chosen, dict):
        return None, None, None

    return chosen.get("branchId"), None, chosen.get("deliveryType")


def resolve_cart_context(
    client, *, print_fn=None, input_fn=None, log_store=None, resolved_address=None
) -> CartContext:
    print_fn = print_fn or print

    my_cart = client.call("silpo_get_my_shopping_cart") or {}
    shopping_cart_id = my_cart.get("shoppingCartId")
    if not shopping_cart_id:
        return _empty_context()

    response = client.call("silpo_get_shopping_cart_by_id", {"shoppingCartId": shopping_cart_id}) or {}
    cart = response.get("cart") or {}
    loyalty = response.get("loyalty") or {}

    shipments = cart.get("shipments") or []
    shipment = shipments[0] if shipments else {}
    timeslot = cart.get("timeslot") or {}
    validations = (cart.get("calculation") or {}).get("validations") or []

    for validation in validations:
        print_fn(f"Cart validation [{validation.get('level')}]: {validation.get('message')}")

    branch_id = shipment.get("branchId")
    company_id = shipment.get("companyId")
    delivery_type = cart.get("deliveryType")

    if not shipments:
        # Fresh account / cleared cart: no delivery context established yet.
        # Fall back to the Address Resolver + silpo_get_available_delivery_types
        # (reused off ResolvedAddress.delivery_types) for real branch/delivery
        # context (issue #29). Reuse the caller's already-resolved address
        # (reorder's pipeline) instead of prompting the user a second time.
        address = resolved_address
        if address is None:
            address = resolve_address(client, log_store or ReorderLogStore(), input_fn=input_fn, print_fn=print_fn)
        if address is not None:
            branch_id, company_id, delivery_type = _branch_context_from_delivery_types(address.delivery_types)

    return CartContext(
        shopping_cart_id=shopping_cart_id,
        branch_id=branch_id,
        company_id=company_id,
        delivery_type=delivery_type,
        timeslot_start=timeslot.get("start"),
        timeslot_end=timeslot.get("end"),
        validations=validations,
        products=shipment.get("products") or [],
        bonus_available=loyalty.get("bonusAvailable"),
        timeslot=cart.get("timeslot"),
        address=cart.get("address"),
        shipments=shipments,
    )
