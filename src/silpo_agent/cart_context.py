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
"""

from dataclasses import dataclass, field


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


def resolve_cart_context(client, *, print_fn=None) -> CartContext:
    print_fn = print_fn or print

    my_cart = client.call("silpo_get_my_shopping_cart") or {}
    shopping_cart_id = my_cart.get("shoppingCartId")
    if not shopping_cart_id:
        return _empty_context()

    response = client.call("silpo_get_shopping_cart_by_id", {"shoppingCartId": shopping_cart_id}) or {}
    cart = response.get("cart") or {}

    shipments = cart.get("shipments") or []
    shipment = shipments[0] if shipments else {}
    timeslot = cart.get("timeslot") or {}
    validations = (cart.get("calculation") or {}).get("validations") or []

    for validation in validations:
        print_fn(f"Cart validation [{validation.get('level')}]: {validation.get('message')}")

    return CartContext(
        shopping_cart_id=shopping_cart_id,
        branch_id=shipment.get("branchId"),
        company_id=shipment.get("companyId"),
        delivery_type=cart.get("deliveryType"),
        timeslot_start=timeslot.get("start"),
        timeslot_end=timeslot.get("end"),
        validations=validations,
        products=shipment.get("products") or [],
    )
