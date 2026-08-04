"""Cart Writer — happy path only (see prd_reorder_optimizer.md's Cart Writer
section). Adds Typical Items to the cart via `silpo_add_or_update_cart_products`
and reports what was added and the total. Cart-only: never calls
checkout/payment (CONTEXT.md's "Reorder flow — cart-only scope").

No cart-guard, no budget cap, no substitution handling here — separate
tickets (#4, #5, #6).

Schema assumption (unverified live, see ../../docs/mcp_schema.md):
`silpo_add_or_update_cart_products` takes `{"items": [{"product_id", "quantity"}]}`
and each Typical Item is added at quantity 1 — the Order Aggregator's Typical
Item doesn't carry a usual-quantity signal, so this ticket assumes 1. The
report's total is computed locally from each item's last known price rather
than trusting a total in the call's response, since that response shape is
also unverified.
"""

from dataclasses import dataclass

from silpo_agent.order_aggregator import TypicalItem


@dataclass(frozen=True)
class CartReport:
    items_added: list[tuple[str, float]]
    total: float


def write_cart(client, typical_items: list[TypicalItem]) -> CartReport:
    if not typical_items:
        return CartReport(items_added=[], total=0.0)

    client.call(
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": item.product_id, "quantity": 1} for item in typical_items]},
    )

    items_added = [(item.product_id, item.last_known_price) for item in typical_items]
    total = sum(price for _, price in items_added)
    return CartReport(items_added=items_added, total=total)
