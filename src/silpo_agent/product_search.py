"""Product Search: read-only `search` subcommand. Free-text product lookup
against the current branch/delivery context, closing the read/act loop:
slugs printed here are exactly what `cart edit --add/--replace` takes,
and Silpo slugs cannot be derived from a product name.

Real schema (docs/mcp_schema.md, live-verified): `silpo_find_products_batch`
takes `{"branchId", "deliveryType", "timeslotStart", "timeslotEnd",
"products": [...], "limit"}` and answers `{"queries": [{"query",
"totalFound", "products": [...]}]}` grouped per input query. Product
records carry `id/name/slug/price/oldPrice/stock/available/companyId/
branchId`. `silpo_get_products` has no free-text filter, so it cannot
serve a name search.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import resolve_cart_context

_PLASTIC_BAG_KEYWORD = "пакет"


@dataclass(frozen=True)
class ProductHit:
    name: str
    price: float | None
    old_price: float | None = None
    stock: int | None = None
    available: bool | None = None
    slug: str | None = None
    product_id: str | None = None

    def format(self) -> str:
        line = f"{self.name}: {self.price if self.price is not None else '?'}"
        if self.old_price is not None and self.price is not None and self.old_price > self.price:
            line += f" (was {self.old_price:.2f})"
        if self.stock is not None:
            line += f" (stock: {self.stock})"
        if self.slug:
            line += f"  {self.slug}"
        return line


def search_products(client, cart_context, query: str, limit: int = 10) -> list[ProductHit]:
    response = (
        client.call(
            "silpo_find_products_batch",
            {
                "branchId": cart_context.branch_id,
                "deliveryType": cart_context.delivery_type,
                "timeslotStart": cart_context.timeslot_start,
                "timeslotEnd": cart_context.timeslot_end,
                "products": [query],
                "limit": limit,
            },
        )
        or {}
    )
    queries = response.get("queries") or []
    entry = next((q for q in queries if q.get("query") == query), queries[0] if queries else {})
    products = entry.get("products") or []
    hits = []
    for product in products[:limit]:
        name = product.get("name") or ""
        if _PLASTIC_BAG_KEYWORD in name.lower():
            continue
        hits.append(
            ProductHit(
                name=name or product.get("id") or "?",
                price=product.get("price"),
                old_price=product.get("oldPrice"),
                stock=product.get("stock"),
                available=product.get("available"),
                slug=product.get("slug"),
                product_id=product.get("id"),
            )
        )
    return hits


def run_search(client, log_store, query: str, limit: int, *, input_fn=None, print_fn=None) -> list[ProductHit]:
    cart_context = resolve_cart_context(client, log_store=log_store, input_fn=input_fn, print_fn=print_fn)
    return search_products(client, cart_context, query, limit)
