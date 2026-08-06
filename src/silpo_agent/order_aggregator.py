"""Order Aggregator: pure function that derives Typical Items from raw online
orders. No network calls, no side effects, fully unit-testable against
fixture order data.

Input order shape (live-verified against `silpo_get_my_online_orders`, see
../../docs/mcp_schema.md's "Order history tools" section): each order is a
dict with a "products" list, each line item a dict with "id", "name",
"price", "quantity", "companyId", "branchId", and a "removed" bool
("removed": true means the item was pulled from the order after ordering
but before delivery, though it's still listed with its original
price/subtotal -- these are skipped when counting frequency and determining
last known price/company/branch/name/quantity). `name` is carried through
so downstream consumers (Cart Writer's plastic-bag filter, issue #19) can
match on it. Orders are returned newest-first by `silpo_get_my_online_orders`
(confirmed live), and are assumed to already be confirmed/paid orders (the
tool's own semantics, per CONTEXT.md's Reorder log entry) -- this function
does not filter on a paid/confirmed status field.

`TypicalItem.quantity` is the arithmetic mean of a product's per-order
`quantity`, averaged only over the orders it actually appeared in -- e.g. a
product bought in 2 of 10 orders averages over those 2, not all 10 (which
would systematically pull every infrequent item's quantity toward zero).
Falls back to 1 for a line missing `quantity` entirely. Not rounded to a
valid add-to-basket step here -- this module has no live product record
(weighted/step) to round against; that happens downstream, where one is
available (see cart_writer.py's `round_to_step`).
"""

from dataclasses import dataclass


class InsufficientOrderHistoryError(Exception):
    """Raised when fewer than `last` orders exist (including zero)."""


@dataclass(frozen=True)
class TypicalItem:
    product_id: str
    frequency: float
    last_known_price: float
    company_id: str | None = None
    branch_id: str | None = None
    name: str | None = None
    quantity: float = 1.0


def derive_typical_items(orders: list[dict], last: int, threshold: float) -> list[TypicalItem]:
    if len(orders) < last:
        raise InsufficientOrderHistoryError(
            f"need at least {last} past orders to determine typical items, found {len(orders)}"
        )

    considered = orders[:last]

    counts: dict[str, int] = {}
    last_known_price: dict[str, float] = {}
    last_known_context: dict[str, tuple[str | None, str | None, str | None]] = {}
    quantity_sum: dict[str, float] = {}
    for order in considered:
        active_lines = [line for line in order.get("products", []) if not line.get("removed", False)]
        for product_id in {line["id"] for line in active_lines}:
            counts[product_id] = counts.get(product_id, 0) + 1
        for line in active_lines:
            product_id = line["id"]
            last_known_price.setdefault(product_id, line["price"])
            last_known_context.setdefault(
                product_id, (line.get("companyId"), line.get("branchId"), line.get("name"))
            )
        # Usual quantity is the arithmetic mean over the orders a product
        # actually appeared in, never diluted by orders it was absent from
        # (dividing by `counts` below, not by len(considered)). Two lines of
        # the same product within one order sum first, so a repeat line
        # counts as that order's single contribution, matching how `counts`
        # above already treats a repeat as one occurrence, not two.
        order_quantity: dict[str, float] = {}
        for line in active_lines:
            order_quantity[line["id"]] = order_quantity.get(line["id"], 0) + line.get("quantity", 1)
        for product_id, qty in order_quantity.items():
            quantity_sum[product_id] = quantity_sum.get(product_id, 0) + qty

    typical = [
        TypicalItem(
            product_id=product_id,
            frequency=count / len(considered),
            last_known_price=last_known_price[product_id],
            company_id=last_known_context[product_id][0],
            branch_id=last_known_context[product_id][1],
            name=last_known_context[product_id][2],
            quantity=quantity_sum[product_id] / count,
        )
        for product_id, count in counts.items()
        if count / len(considered) >= threshold
    ]
    typical.sort(key=lambda item: (-item.frequency, item.product_id))
    return typical
