"""Cart Writer (see prd_reorder_optimizer.md's Cart Writer section). Before
adding anything, reads the current cart via `silpo_get_my_shopping_cart` and
warns the user if it already has items from a prior session (CONTEXT.md's
"Non-empty cart guard" — warn-and-proceed-or-abort, never auto-clear or
silently merge). Applies the optional `--budget` cap by trimming the
lowest-priority Typical Items (by frequency) until the total fits, if set —
otherwise just totals and reports. Adds the (possibly trimmed) items via
`silpo_add_or_update_cart_products` and reports what was added, trimmed, and
the total. Cart-only: never calls checkout/payment (CONTEXT.md's "Reorder
flow — cart-only scope").

Schema assumptions (unverified live, see ../../docs/mcp_schema.md):
`silpo_add_or_update_cart_products` takes `{"items": [{"product_id", "quantity"}]}`
and each Typical Item is added at quantity 1 — the Order Aggregator's Typical
Item doesn't carry a usual-quantity signal, so this ticket assumes 1. The
report's total is computed locally from each item's last known price rather
than trusting a total in the call's response, since that response shape is
also unverified. `silpo_get_my_shopping_cart` is assumed to return a dict
with an `"items"` list (empty/absent means an empty cart) — see
../../docs/mcp_schema.md for the new assumption recorded for this ticket.
"""

from dataclasses import dataclass, field

from silpo_agent.order_aggregator import TypicalItem


@dataclass(frozen=True)
class CartReport:
    items_added: list[tuple[str, float]]
    total: float
    trimmed: list[tuple[str, float]] = field(default_factory=list)
    aborted: bool = False


def _cart_has_items(cart_response) -> bool:
    items = cart_response.get("items") if isinstance(cart_response, dict) else cart_response
    return bool(items)


def _trim_to_budget(items: list[TypicalItem], budget: float) -> tuple[list[TypicalItem], list[TypicalItem]]:
    """Drops lowest-priority (lowest frequency) items first until the total
    fits under `budget`. Returns (kept, trimmed), both in original order.
    """
    total = sum(item.last_known_price for item in items)
    if total <= budget:
        return items, []

    drop_order = sorted(items, key=lambda item: (item.frequency, item.product_id))
    dropped_ids: set[str] = set()
    for item in drop_order:
        if total <= budget:
            break
        dropped_ids.add(item.product_id)
        total -= item.last_known_price

    kept = [item for item in items if item.product_id not in dropped_ids]
    trimmed = [item for item in items if item.product_id in dropped_ids]
    return kept, trimmed


def write_cart(
    client,
    typical_items: list[TypicalItem],
    *,
    budget: float | None = None,
    input_fn=None,
    print_fn=None,
) -> CartReport:
    input_fn = input_fn or input
    print_fn = print_fn or print

    if not typical_items:
        return CartReport(items_added=[], total=0.0)

    current_cart = client.call("silpo_get_my_shopping_cart")
    if _cart_has_items(current_cart):
        print_fn("Warning: your cart already has items from a previous session.")
        answer = input_fn("Add to the existing cart anyway? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print_fn("Aborted: cart left unchanged.")
            return CartReport(items_added=[], total=0.0, aborted=True)

    items_to_add = typical_items
    trimmed_items: list[TypicalItem] = []
    if budget is not None:
        items_to_add, trimmed_items = _trim_to_budget(typical_items, budget)
    trimmed = [(item.product_id, item.last_known_price) for item in trimmed_items]

    if not items_to_add:
        return CartReport(items_added=[], total=0.0, trimmed=trimmed)

    client.call(
        "silpo_add_or_update_cart_products",
        {"items": [{"product_id": item.product_id, "quantity": 1} for item in items_to_add]},
    )

    items_added = [(item.product_id, item.last_known_price) for item in items_to_add]
    total = sum(price for _, price in items_added)
    return CartReport(items_added=items_added, total=total, trimmed=trimmed)
