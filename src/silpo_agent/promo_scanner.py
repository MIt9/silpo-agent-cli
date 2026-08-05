"""Promo Scanner: powers the read-only `deals` subcommand (issue #32, part
of #26's cart_and_deals PRD). Independent of the user's cart -- discovers
store-wide discounts rather than starting from a specific item (that's the
Similar-Products Promo Finder's job, per `cart edit`/`cart promos`, not this
module's).

Real schema (see ../../docs/mcp_schema.md's "Promo / loyalty tools" and
"Product search / replacement tools" sections, live-verified):
- `silpo_get_promotions({"branchId", "deliveryType", "timeslotStart",
  "timeslotEnd"})` -> `{"success", "summary", "promotions": [{"code",
  "title", "productCount", "url"}]}` -- branch-wide promotion *categories*,
  not per-product results.
- `silpo_get_products({"branchId", "deliveryType", "timeslotStart",
  "timeslotEnd", "mustHavePromotion": true, "promotionCode": "<code>"})` is
  the only real way to list a category's actual promo products. Confirmed
  live that some categories list thousands of products (one had 3517), so
  every category response is capped locally to `cap_per_category` (~20)
  before merging -- this cap is applied defensively regardless of whether
  the live server actually honors the `limit` param passed alongside the
  other filters, since that hasn't been confirmed.
- Product record shape is the same one documented for `silpo_get_products` /
  `silpo_find_products_batch` / `silpo_get_similar_products`: `{"id",
  "name", ..., "price", "oldPrice", ...}`. A product only counts as a real
  deal when `oldPrice` is a genuinely higher number than `price` -- same
  discount signal the PRD's Similar-Products Promo Finder uses.

Plastic-bag-style items (пакет / пакет з пакетів / пакет-майка) are never a
"deal" a user would search for -- filtered with the same
`cart_writer.py`'s `_is_plastic_bag` substring-on-name heuristic, duplicated
here since it operates on raw product dicts (from `silpo_get_products`), not
a `TypicalItem`.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import CartContext

_PLASTIC_BAG_KEYWORD = "пакет"
DEFAULT_CATEGORY_CAP = 20


@dataclass(frozen=True)
class Deal:
    product_id: str
    name: str
    # Issue #50: the product record's own slug, carried through untouched --
    # it's this CLI's public product identifier (what `cart edit --replace`
    # takes) and the only one `silpo_get_product_details` can resolve.
    slug: str | None
    price: float
    old_price: float
    discount_pct: float
    company_id: str | None
    branch_id: str | None


def _is_plastic_bag(product: dict) -> bool:
    name = product.get("name")
    return bool(name) and _PLASTIC_BAG_KEYWORD in name.lower()


def _discount_pct(price, old_price) -> float | None:
    """None (not a real deal) unless both are numbers and old_price is
    genuinely higher than price -- same signal the PRD's Similar-Products
    Promo Finder uses (`oldPrice` present and higher than `price`)."""
    if not isinstance(price, (int, float)) or not isinstance(old_price, (int, float)):
        return None
    if old_price <= price:
        return None
    return (old_price - price) / old_price * 100


def scan_deals(
    client, cart_context: CartContext, *, limit: int = 10, cap_per_category: int = DEFAULT_CATEGORY_CAP
) -> list[Deal]:
    promos_response = client.call(
        "silpo_get_promotions",
        {
            "branchId": cart_context.branch_id,
            "deliveryType": cart_context.delivery_type,
            "timeslotStart": cart_context.timeslot_start,
            "timeslotEnd": cart_context.timeslot_end,
        },
    ) or {}
    promotions = promos_response.get("promotions") or []

    deals: list[Deal] = []
    for promo in promotions:
        code = promo.get("code")
        if not code:
            continue

        products_response = client.call(
            "silpo_get_products",
            {
                "branchId": cart_context.branch_id,
                "deliveryType": cart_context.delivery_type,
                "timeslotStart": cart_context.timeslot_start,
                "timeslotEnd": cart_context.timeslot_end,
                "mustHavePromotion": True,
                "promotionCode": code,
                "limit": cap_per_category,
            },
        ) or {}
        products = (products_response.get("products") or [])[:cap_per_category]

        for product in products:
            if _is_plastic_bag(product):
                continue
            pct = _discount_pct(product.get("price"), product.get("oldPrice"))
            if pct is None:
                continue
            deals.append(
                Deal(
                    product_id=product.get("id"),
                    name=product.get("name") or "",
                    slug=product.get("slug"),
                    price=product.get("price"),
                    old_price=product.get("oldPrice"),
                    discount_pct=pct,
                    company_id=product.get("companyId"),
                    branch_id=product.get("branchId"),
                )
            )

    deals.sort(key=lambda deal: deal.discount_pct, reverse=True)
    return deals[:limit]
