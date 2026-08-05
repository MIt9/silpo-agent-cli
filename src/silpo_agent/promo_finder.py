"""Similar-Products Promo Finder (issue #28, shared by `cart promos` and,
per issue #31, `cart edit`'s promo-browse replacement path).

Given a product's `slug` (cart items already carry their own `slug` -- see
`cart.shipments[0].products[].slug` in docs/mcp_schema.md's "Cart tools"
section), calls `silpo_get_similar_products` and filters the results to
candidates that are genuinely discounted right now (`oldPrice` present and
strictly greater than `price`), ranked by discount size (`oldPrice - price`,
descending).

Deliberately NOT category-based or keyword/text-matching: confirmed live
that no product record anywhere in this API carries a category id (see
docs/mcp_schema.md's "Product search / replacement tools" section), so
Silpo's own similarity engine (`silpo_get_similar_products`) is used as the
real signal instead, same design decision already made for
`promo_optimizer.py` (issue #20) and `coupons_lister.py` (issue #36).

Real schema (live-verified, docs/mcp_schema.md's "Product search /
replacement tools" section):
- `silpo_get_similar_products({"branchId", "slug", "deliveryType", "limit",
  "offset"})` -> `{"success", "summary", "products": [...], "meta":
  {"total"}}`, `products[]` using the general product-record shape
  (`"id"`, `"name"`, `"slug"`, `"price"`, `"oldPrice"`, `"companyId"`,
  `"branchId"`, ...).

Plastic-bag-style candidates (e.g. "пакет майка") are filtered out before
ranking, same convention as `cart_writer.py`'s `_is_plastic_bag` (matched
case-insensitively on the candidate's `name` containing "пакет") -- bags are
added automatically at order fulfillment and should never surface as a
promo alternative (docs/mcp_schema.md / issue #26 PRD).

Public interface: `find_promo_alternatives(client, cart_context, slug,
*, limit=10) -> list[PromoCandidate]`. Takes a plain `client` +
`CartContext` (for `branchId`/`deliveryType`) + `slug`, not anything
CLI-specific, so issue #31's `cart edit` promo-browse path can call it
as-is with no duplicated similarity/discount-filtering logic.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import CartContext

_PLASTIC_BAG_KEYWORD = "пакет"


@dataclass(frozen=True)
class PromoCandidate:
    product_id: str | None
    name: str
    slug: str | None
    price: float
    old_price: float
    discount: float
    company_id: str | None
    branch_id: str | None


def _is_plastic_bag(name: str) -> bool:
    return bool(name) and _PLASTIC_BAG_KEYWORD in name.lower()


def _is_discounted(product: dict) -> bool:
    price = product.get("price")
    old_price = product.get("oldPrice")
    return price is not None and old_price is not None and old_price > price


def find_promo_alternatives(
    client, cart_context: CartContext, slug: str, *, limit: int = 10
) -> list[PromoCandidate]:
    response = (
        client.call(
            "silpo_get_similar_products",
            {
                "branchId": cart_context.branch_id,
                "slug": slug,
                "deliveryType": cart_context.delivery_type,
                "limit": limit,
            },
        )
        or {}
    )

    products = response.get("products") or []
    candidates = [
        PromoCandidate(
            product_id=product.get("id"),
            name=product.get("name") or "",
            slug=product.get("slug"),
            price=product["price"],
            old_price=product["oldPrice"],
            discount=product["oldPrice"] - product["price"],
            company_id=product.get("companyId"),
            branch_id=product.get("branchId"),
        )
        for product in products
        if isinstance(product, dict) and _is_discounted(product) and not _is_plastic_bag(product.get("name") or "")
    ]

    candidates.sort(key=lambda c: c.discount, reverse=True)
    return candidates
