"""Order Aggregator: pure function that derives Typical Items from raw online
orders. No network calls, no side effects, fully unit-testable against
fixture order data.

Input order shape assumption (unverified against live `tools/list` — see
../../docs/mcp_schema.md): each order is a dict with an "items" list, each
line item a dict with "product_id" and "price". Orders are assumed to be
returned newest-first by `silpo_get_my_online_orders`, and to already be
confirmed/paid orders (the tool's own semantics, per CONTEXT.md's Reorder
log entry) — this function does not filter on a paid/confirmed status field.
"""

from dataclasses import dataclass


class InsufficientOrderHistoryError(Exception):
    """Raised when fewer than `last` orders exist (including zero)."""


@dataclass(frozen=True)
class TypicalItem:
    product_id: str
    frequency: float
    last_known_price: float


def derive_typical_items(orders: list[dict], last: int, threshold: float) -> list[TypicalItem]:
    if len(orders) < last:
        raise InsufficientOrderHistoryError(
            f"need at least {last} past orders to determine typical items, found {len(orders)}"
        )

    considered = orders[:last]

    counts: dict[str, int] = {}
    last_known_price: dict[str, float] = {}
    for order in considered:
        for product_id in {line["product_id"] for line in order.get("items", [])}:
            counts[product_id] = counts.get(product_id, 0) + 1
        for line in order.get("items", []):
            last_known_price.setdefault(line["product_id"], line["price"])

    typical = [
        TypicalItem(
            product_id=product_id,
            frequency=count / len(considered),
            last_known_price=last_known_price[product_id],
        )
        for product_id, count in counts.items()
        if count / len(considered) >= threshold
    ]
    typical.sort(key=lambda item: (-item.frequency, item.product_id))
    return typical
