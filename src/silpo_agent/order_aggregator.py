"""Order Aggregator: pure function that derives Typical Items from raw online
orders. No network calls, no side effects, fully unit-testable against
fixture order data.

Input order shape (live-verified against `silpo_get_my_online_orders`, see
../../docs/mcp_schema.md's "Order history tools" section): each order is a
dict with a "products" list, each line item a dict with "id", "name",
"price", "companyId", "branchId", and a "removed" bool ("removed": true
means the item was pulled from the order after ordering but before
delivery, though it's still listed with its original price/subtotal --
these are skipped when counting frequency and determining last known
price/company/branch/name). `name` is carried through so downstream
consumers (Cart Writer's plastic-bag filter, issue #19) can match on it.
Orders are returned newest-first by `silpo_get_my_online_orders` (confirmed
live), and are assumed to already be confirmed/paid orders (the tool's own
semantics, per CONTEXT.md's Reorder log entry) -- this function does not
filter on a paid/confirmed status field.
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


def derive_typical_items(orders: list[dict], last: int, threshold: float) -> list[TypicalItem]:
    if len(orders) < last:
        raise InsufficientOrderHistoryError(
            f"need at least {last} past orders to determine typical items, found {len(orders)}"
        )

    considered = orders[:last]

    counts: dict[str, int] = {}
    last_known_price: dict[str, float] = {}
    last_known_context: dict[str, tuple[str | None, str | None, str | None]] = {}
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

    typical = [
        TypicalItem(
            product_id=product_id,
            frequency=count / len(considered),
            last_known_price=last_known_price[product_id],
            company_id=last_known_context[product_id][0],
            branch_id=last_known_context[product_id][1],
            name=last_known_context[product_id][2],
        )
        for product_id, count in counts.items()
        if count / len(considered) >= threshold
    ]
    typical.sort(key=lambda item: (-item.frequency, item.product_id))
    return typical
