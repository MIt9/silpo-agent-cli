"""Promo Optimizer: opt-in module (`--optimize promos`, see cli.py), invoked
only when the flag is passed. Runs on the Substitution Resolver's resolved
item set, between it and the Cart Writer.

Scope decision (issue #20 -- see docs/mcp_schema.md's "Promo / loyalty
tools" section for the full live-verified schema this is built against):

Narrowed to bonus application only. The original design assumed two tools
that don't exist on the real MCP server (`silpo_get_promo_equivalent`,
`silpo_get_available_bonuses`). The real promo-swap mechanism --
`silpo_get_products({"mustHavePromotion": true, "promotionCode": ...})` --
is a category/text browse, not a per-product lookup: there's no verified
free-text search parameter on that tool (only `category`/`mustHavePromotion`/
`promotionCode`/`set` filters are documented), so matching a Typical Item to
"its" promo equivalent would mean guessing an unconfirmed filter and/or
scanning entire promo categories by name -- exactly the unreliable heuristic
issue #20 explicitly permits dropping. Bonus application, by contrast, is a
single well-specified field on `silpo_update_shopping_cart` fed by a value
(`bonus_available`) already returned by the same `silpo_get_shopping_cart_by_id`
call the Cart Context Resolver (#17) makes -- no new lookup, no matching
heuristic, nothing speculative. `promoCode` is left `null`:
`silpo_get_promo_codes()` returned empty for the live test account
(unverified case, not worth building against per the issue).

Real schema (live-verified, see ../../docs/mcp_schema.md):
- `silpo_update_shopping_cart` is the real "apply bonuses" call --
  `bonusRequested` (number) and `promoCode` (string or null) are two more
  fields on it, alongside `shipments`/`address`/`timeslot`/`deliveryType`,
  which per the tool's own description must be copied from
  `silpo_get_shopping_cart_by_id`'s response as-is, not constructed. Those
  raw values are carried by `CartContext` (extended in cart_context.py for
  this ticket: `.timeslot`/`.address`/`.shipments`/`.bonus_available`).

Off by default: `cli.py` only imports/calls this module when
`--optimize promos` is passed, so a plain `reorder` makes zero calls here
(verified in test_cli.py's flag-off test).
"""

from dataclasses import dataclass

from silpo_agent.cart_context import CartContext
from silpo_agent.order_aggregator import TypicalItem


@dataclass(frozen=True)
class PromoResult:
    items: list[TypicalItem]
    bonus_applied: float | None = None


def optimize_promos(client, items: list[TypicalItem], cart_context: CartContext) -> PromoResult:
    bonus = cart_context.bonus_available
    # silpo_update_shopping_cart requires shoppingCartId/deliveryType/timeslot/
    # address/shipments all present (the last four copied from
    # silpo_get_shopping_cart_by_id verbatim, per that tool's own
    # description) -- a resolved-but-incomplete CartContext (e.g. no
    # shipments recorded yet) must skip the call rather than send nulls
    # into required fields, same guard style as substitution_resolver.py's
    # no-cart-context skip (issue #18).
    has_required_context = (
        cart_context.shopping_cart_id
        and cart_context.delivery_type
        and cart_context.timeslot
        and cart_context.address
        and cart_context.shipments
    )
    if not bonus or not has_required_context:
        return PromoResult(items=items)

    response = (
        client.call(
            "silpo_update_shopping_cart",
            {
                "shoppingCartId": cart_context.shopping_cart_id,
                "deliveryType": cart_context.delivery_type,
                "timeslot": cart_context.timeslot,
                "address": cart_context.address,
                "shipments": cart_context.shipments,
                "bonusRequested": bonus,
                "promoCode": None,
            },
        )
        or {}
    )
    # Mutating call -- don't claim the bonus was applied unless the response
    # says so (every real response in this MCP server is enveloped in
    # {"success": bool, ...}, per docs/mcp_schema.md).
    if not response.get("success"):
        return PromoResult(items=items)

    return PromoResult(items=items, bonus_applied=bonus)
