"""Cart Writer (see prd_reorder_optimizer.md's Cart Writer section). Before
adding anything, reads the current cart's real contents from the caller's
already-resolved `CartContext` (issue #17's `resolve_cart_context`) and
warns the user if it already has items from a prior session (CONTEXT.md's
"Non-empty cart guard" -- warn-and-proceed-or-abort, never auto-clear or
silently merge). Applies the optional `--budget` cap by trimming the
lowest-priority Typical Items (by frequency) until the existing cart's own
payable total (`CartContext.total_after_discounts`) plus the new items fits,
if set -- otherwise just totals and reports. A reorder onto a non-empty
cart counts what's already there against the budget, rather than treating
`--budget` as a cap on the new items alone. Adds the (possibly trimmed) items via
`silpo_add_or_update_cart_products` and reports what was added, trimmed, and
the total. Cart-only: never calls checkout/payment (CONTEXT.md's "Reorder
flow -- cart-only scope").

Real schema (see ../../docs/mcp_schema.md's "Cart tools" section,
live-verified):
- `silpo_get_my_shopping_cart` returns only `{"success", "shoppingCartId"}`
  -- no cart contents, so it is not read here at all. The non-empty-cart
  guard instead reads `CartContext.products` (issue #17's resolver, which
  already made the `silpo_get_my_shopping_cart` -> `silpo_get_shopping_cart_by_id`
  round trip and carries `cart.shipments[0].products`), avoiding a
  duplicate network call.
- `silpo_add_or_update_cart_products` takes `{"shoppingCartId", "products":
  [{"productId", "companyId", "branchId", "quantity", "addQuantity", "comment"}]}`.
  `companyId`/`branchId` come from the Typical Item (Order Aggregator, #16)
  or a substitution/promo-resolved item; if either is missing (e.g. a
  substituted item that didn't carry it through) this falls back to the
  CartContext's own branch/company, since a cart's items all live under one
  branch/company already. Each Typical Item is added at quantity 1 with
  `addQuantity=True` (adds to, rather than overwrites, any existing
  quantity) -- the Order Aggregator's Typical Item doesn't carry a
  usual-quantity signal, so this ticket assumes 1, same as before.
  `comment` is always null; nothing upstream produces one.
- Plastic-bag-style items (пакет / пакет з пакетів / пакет-майка) are
  always skipped before the add call, per the tool's own guidance -- see
  `_is_plastic_bag`. Matched case-insensitively on the item's `name`
  containing "пакет"; this is a heuristic (Ukrainian-only, substring match)
  -- good enough for the known plastic-bag SKUs in order history, but an
  item that lost its `name` during substitution (issue #18's territory)
  won't be caught. ponytail: substring-on-name heuristic, revisit with a
  dedicated category/tag from the product record if false negatives show up.

The report's total is still computed locally from each item's last known
price rather than trusting a total in the add call's response, since that
response shape remains unverified (mutating tool, not exercised live).
"""

from dataclasses import dataclass, field

from silpo_agent.cart_context import CartContext
from silpo_agent.order_aggregator import TypicalItem

_PLASTIC_BAG_KEYWORD = "пакет"


@dataclass(frozen=True)
class CartReport:
    items_added: list[tuple[str, float]]
    total: float
    trimmed: list[tuple[str, float]] = field(default_factory=list)
    aborted: bool = False


def _is_plastic_bag(item: TypicalItem) -> bool:
    return bool(item.name) and _PLASTIC_BAG_KEYWORD in item.name.lower()


def _trim_to_budget(
    items: list[TypicalItem], budget: float, already_spent: float = 0.0
) -> tuple[list[TypicalItem], list[TypicalItem]]:
    """Drops lowest-priority (lowest frequency) items first until
    `already_spent` (the existing cart's own payable total, for a reorder
    onto a non-empty cart) plus the new items' total fits under `budget`.
    Returns (kept, trimmed), both in original order.
    """
    total = already_spent + sum(item.last_known_price for item in items)
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
    cart_context: CartContext,
    *,
    budget: float | None = None,
    input_fn=None,
    print_fn=None,
) -> CartReport:
    input_fn = input_fn or input
    print_fn = print_fn or print

    if not typical_items:
        return CartReport(items_added=[], total=0.0)

    purchasable_items = [item for item in typical_items if not _is_plastic_bag(item)]

    if cart_context.products:
        print_fn("Warning: your cart already has items from a previous session.")
        answer = input_fn("Add to the existing cart anyway? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print_fn("Aborted: cart left unchanged.")
            return CartReport(items_added=[], total=0.0, aborted=True)

        existing_ids = {p.get("productId") for p in cart_context.products}
        purchasable_items = [item for item in purchasable_items if item.product_id not in existing_ids]

    items_to_add = purchasable_items
    trimmed_items: list[TypicalItem] = []
    if budget is not None:
        already_spent = cart_context.total_after_discounts or 0.0
        items_to_add, trimmed_items = _trim_to_budget(purchasable_items, budget, already_spent)
    trimmed = [(item.product_id, item.last_known_price) for item in trimmed_items]

    if not items_to_add:
        return CartReport(items_added=[], total=0.0, trimmed=trimmed)

    client.call(
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": cart_context.shopping_cart_id,
            "products": [
                {
                    "productId": item.product_id,
                    "companyId": item.company_id or cart_context.company_id,
                    "branchId": item.branch_id or cart_context.branch_id,
                    "quantity": 1,
                    "addQuantity": True,
                }
                for item in items_to_add
            ],
        },
    )

    items_added = [(item.product_id, item.last_known_price) for item in items_to_add]
    total = sum(price for _, price in items_added)
    return CartReport(items_added=items_added, total=total, trimmed=trimmed)
