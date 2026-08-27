"""Favorites Deals: read-only `favorites-deals` subcommand (issue #33, part
of #26). Checks the user's own favorites list for items currently
discounted -- no matching/heuristic needed, it's already the user's own
explicit list, unlike Substitution Resolver's stock-availability matching.

Real schema (see ../../docs/mcp_schema.md's "Profile / account tools"
section): `silpo_get_my_favorites({"branchId", "deliveryType",
"timeslotStart", "limit", "offset"})` -- not live-verified at implementation
time, but per its tool description returns products in the same shape as
`silpo_get_products` (`silpo_get_similar_products`'s confirmed live shape is
`{"success", "summary", "products": [...], "meta": {"total"}}`, the closest
verified analogue) -- so this module reads the `"products"` key, same
defensive `.get(...) or []` pattern used everywhere else in this codebase.
Each product record's `price`/`oldPrice` fields are the same ones
live-verified on cart/order product records elsewhere in this project.

Branch/delivery/timeslot context comes from `cart_context.resolve_cart_context`
(issue #17, with the issue #29 no-shipments address-resolver fallback) --
this module is one of the "future callers" #29's docstring named as calling
`resolve_cart_context` directly with no address of its own, so the
interactive confirm/pick/new-address flow runs automatically on a
fresh/cleared-cart account.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import resolve_cart_context


@dataclass(frozen=True)
class FavoriteDeal:
    name: str
    price: float
    old_price: float
    # Issue #50: the product record's own slug -- this CLI's public product
    # identifier, what `cart edit --replace` takes. Omitted from the
    # formatted line when absent rather than rendered as a placeholder: an
    # identifier that can't be resolved is worse than no identifier.
    slug: str | None = None
    # Ticket 02 (smart-cart): the real product id, same id space as
    # TypicalItem.product_id / order history's "id" -- needed so smart-cart
    # can dedupe a favorited-and-discounted product against typical items and
    # add it via silpo_add_or_update_cart_products. Not shown in format(),
    # which stays slug-based for cart edit --replace.
    product_id: str | None = None

    def format(self) -> str:
        line = f"{self.name}: {self.price:.2f} (was {self.old_price:.2f})"
        if self.slug:
            line += f"  {self.slug}"
        return line


def fetch_favorite_products(client, cart_context) -> list[dict]:
    """Raw `silpo_get_my_favorites` product records (same shape as
    `silpo_get_products`), no discount filter -- `list_favorites_deals` keeps
    only the discounted ones, but smart-cart's `--fill-to` pool wants the
    whole favorites list (a known preference beats a random store deal even
    at full price)."""
    response = (
        client.call(
            "silpo_get_my_favorites",
            {
                "branchId": cart_context.branch_id,
                "deliveryType": cart_context.delivery_type,
                "timeslotStart": cart_context.timeslot_start,
            },
        )
        or {}
    )
    return response.get("products") or []


def list_favorites_deals(
    client, log_store=None, *, input_fn=None, print_fn=None, cart_context=None, products=None
) -> list[FavoriteDeal]:
    # Ticket 02 (smart-cart): a caller that already resolved a CartContext of
    # its own (same pattern as resolve_cart_context's own `resolved_address`,
    # cart_context.py:44-49) passes it here to skip this redundant
    # silpo_get_my_shopping_cart/silpo_get_shopping_cart_by_id round trip --
    # and, on a fresh/cleared cart, avoid re-prompting for address
    # confirmation a second time. The standalone `favorites-deals` command has
    # no context of its own, so it omits this and resolves one here as before.
    # `products` lets smart-cart's --fill-to path share the one
    # `fetch_favorite_products` call it already makes for its fill pool.
    if cart_context is None:
        cart_context = resolve_cart_context(client, log_store=log_store, input_fn=input_fn, print_fn=print_fn)

    if products is None:
        products = fetch_favorite_products(client, cart_context)

    deals = []
    for product in products:
        price = product.get("price")
        old_price = product.get("oldPrice")
        if price is not None and old_price is not None and old_price > price:
            deals.append(
                FavoriteDeal(
                    name=product.get("name") or "",
                    price=price,
                    old_price=old_price,
                    slug=product.get("slug"),
                    product_id=product.get("id"),
                )
            )
    return deals
