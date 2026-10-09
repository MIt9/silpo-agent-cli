"""Order History: read-only `orders` subcommand. Surfaces the same order
records `reorder` aggregates internally, so `--last/--threshold` tuning
stops being a blind guess.

Real schema (docs/mcp_schema.md, live-verified):
- `silpo_get_my_online_orders({"limit", "offset"})` ->
  `{"orders": [{"orderId", "number", "status", "createdAt", "amount",
  "products": [{"id", "name", "price", "quantity", "removed", ...}]}], "meta"}`.
  Newest-first; `removed: true` lines are excluded from the count.
- `silpo_get_my_offline_orders({"branchId", "deliveryType",
  "timeslotStart", "timeslotEnd", "limit" (max 10), "offset"})` -> in-store
  receipts with `filialName/createdAt/sumReg/products[]`; a product with
  `catalogProduct !== null` is reorderable online.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import resolve_cart_context


@dataclass(frozen=True)
class OrderSummary:
    order_id: str | None
    number: str | None
    created_at: str | None
    amount: float | None
    product_count: int
    extra: str = ""

    def format(self) -> str:
        label = self.number or self.order_id or "?"
        amount = f"{self.amount:.2f}" if isinstance(self.amount, (int, float)) else "?"
        when = self.created_at or "?"
        line = f"{label} ({when}): {amount} [{self.product_count} item(s)]"
        if self.extra:
            line += f" -- {self.extra}"
        return line


def _count_online_products(order: dict) -> int:
    return sum(1 for p in order.get("products") or [] if not p.get("removed"))


def list_online_orders(client, limit: int = 5) -> list[OrderSummary]:
    response = client.call("silpo_get_my_online_orders", {"limit": min(limit, 100)}) or {}
    orders = response.get("orders") if isinstance(response, dict) else response
    if not isinstance(orders, list):
        orders = []
    summaries = []
    for order in orders[:limit]:
        summaries.append(
            OrderSummary(
                order_id=order.get("orderId"),
                number=order.get("number"),
                created_at=order.get("createdAt"),
                amount=order.get("amount"),
                product_count=_count_online_products(order),
            )
        )
    return summaries


def list_offline_orders(client, cart_context, limit: int = 5) -> list[OrderSummary]:
    response = (
        client.call(
            "silpo_get_my_offline_orders",
            {
                "branchId": cart_context.branch_id,
                "deliveryType": cart_context.delivery_type,
                "timeslotStart": cart_context.timeslot_start,
                "timeslotEnd": cart_context.timeslot_end,
                "limit": min(limit, 10),
                "offset": 0,
            },
        )
        or {}
    )
    orders = response.get("orders") or []
    summaries = []
    for order in orders[:limit]:
        products = order.get("products") or []
        reorderable = sum(1 for p in products if p.get("catalogProduct"))
        summaries.append(
            OrderSummary(
                order_id=order.get("filId") or order.get("orderId"),
                number=order.get("filialName") or order.get("chequeMagicName"),
                created_at=order.get("createdAt"),
                amount=order.get("sumReg"),
                product_count=len(products),
                extra=f"{reorderable} reorderable" if products else "",
            )
        )
    return summaries


def run_orders(client, log_store, limit: int, *, offline: bool = False, input_fn=None, print_fn=None):
    if not offline:
        return list_online_orders(client, limit)
    cart_context = resolve_cart_context(client, log_store=log_store, input_fn=input_fn, print_fn=print_fn)
    return list_offline_orders(client, cart_context, limit)
