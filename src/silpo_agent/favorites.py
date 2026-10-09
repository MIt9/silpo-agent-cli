"""Favorites: read-only `favorites` list plus `favorites add/remove`
management. Complements `favorites-deals` (discounted subset only):
this module lists the whole explicit-likes list and mutates it.

Real schema (docs/mcp_schema.md): `silpo_get_my_favorites` returns
products in the `silpo_get_products` shape (`{"products": [...]}`);
`silpo_add_or_update_favorite_products` takes `{"actions":
[{"productId", "externalProductId", "toDelete"}]}`, max 5 per call.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import resolve_cart_context
from silpo_agent.cart_editor import resolve_product_by_slug
from silpo_agent.favorites_deals import fetch_favorite_products


@dataclass(frozen=True)
class FavoriteLine:
    name: str
    price: float | None = None
    old_price: float | None = None
    slug: str | None = None
    product_id: str | None = None

    def format(self) -> str:
        line = f"{self.name}: {self.price if self.price is not None else '?'}"
        if self.old_price is not None and self.price is not None and self.old_price > self.price:
            line += f" (was {self.old_price:.2f})"
        if self.slug:
            line += f"  {self.slug}"
        return line


class FavoriteError(Exception):
    pass


def list_favorites(client, log_store=None, *, input_fn=None, print_fn=None, cart_context=None) -> list[FavoriteLine]:
    if cart_context is None:
        cart_context = resolve_cart_context(client, log_store=log_store, input_fn=input_fn, print_fn=print_fn)
    products = fetch_favorite_products(client, cart_context)
    return [
        FavoriteLine(
            name=p.get("name") or p.get("id") or "?",
            price=p.get("price"),
            old_price=p.get("oldPrice"),
            slug=p.get("slug"),
            product_id=p.get("id"),
        )
        for p in products
    ]


def add_favorite(client, cart_context, slug: str) -> FavoriteLine:
    product = resolve_product_by_slug(client, cart_context, slug)
    if product is None or not (product.get("id") or product.get("productId")):
        raise FavoriteError(f"cart edit: product {slug!r} not found")
    product_id = product.get("id") or product.get("productId")
    client.call("silpo_add_or_update_favorite_products", {"actions": [{"productId": product_id, "toDelete": False}]})
    return FavoriteLine(
        name=product.get("name") or product_id,
        price=product.get("price"),
        old_price=product.get("oldPrice"),
        slug=product.get("slug"),
        product_id=product_id,
    )


def remove_favorite(client, cart_context, slug: str) -> FavoriteLine:
    products = fetch_favorite_products(client, cart_context)
    match = next((p for p in products if p.get("slug") == slug), None)
    if match is None:
        raise FavoriteError(f"{slug!r} is not in your favorites.")
    client.call(
        "silpo_add_or_update_favorite_products", {"actions": [{"productId": match.get("id"), "toDelete": True}]}
    )
    return FavoriteLine(
        name=match.get("name") or slug,
        price=match.get("price"),
        old_price=match.get("oldPrice"),
        slug=slug,
        product_id=match.get("id"),
    )
