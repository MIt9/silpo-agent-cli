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

    def format(self) -> str:
        line = f"{self.name}: {self.price:.2f} (was {self.old_price:.2f})"
        if self.slug:
            line += f"  {self.slug}"
        return line


def list_favorites_deals(client, log_store=None, *, input_fn=None, print_fn=None) -> list[FavoriteDeal]:
    cart_context = resolve_cart_context(client, log_store=log_store, input_fn=input_fn, print_fn=print_fn)

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
    products = response.get("products") or []

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
                )
            )
    return deals
