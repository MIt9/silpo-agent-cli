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
_CATEGORY_PAGE_SIZE = 1000


class CategoryNotFoundError(Exception):
    """Raised by `resolve_category_slug` when --category's free-text query
    (e.g. "овочі") doesn't match any real category title, so `scan_deals`
    never sends a bogus category slug to `silpo_get_products` (a slug that
    doesn't exist there just silently returns zero products, indistinguishable
    from "no deals right now" -- this needs to be a clear, distinct error)."""


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


def _deals_from_products(products: list[dict], cap: int) -> list[Deal]:
    deals: list[Deal] = []
    for product in products[:cap]:
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
    return deals


def _fetch_all_categories(client, cart_context: CartContext) -> list[dict]:
    """`silpo_get_categories` is a flat, paginated list (1000+ categories
    real-world) unlike `silpo_get_categories_tree`'s nested-but-nameless
    nodes (its entries carry only slug/children/total, no title) -- this is
    the only listing call that pairs a human title with a real slug."""
    categories: list[dict] = []
    offset = 0
    while True:
        response = (
            client.call(
                "silpo_get_categories",
                {"branchId": cart_context.branch_id, "limit": _CATEGORY_PAGE_SIZE, "offset": offset},
            )
            or {}
        )
        page = response.get("categories") or []
        categories.extend(page)
        total = (response.get("meta") or {}).get("total", len(categories))
        offset += _CATEGORY_PAGE_SIZE
        if not page or offset >= total:
            break
    return categories


@dataclass(frozen=True)
class CategoryMatch:
    slug: str
    title: str
    exact: bool


def resolve_category_from_list(categories: list[dict], query: str) -> CategoryMatch | None:
    """Same matching logic as `resolve_category`, against an already-fetched
    category list -- lets a caller that needs to match several queries
    (norm_top_up.py's per-norm-category-tag lookup) fetch the paginated
    category tree once and reuse it, instead of `resolve_category` calling
    `_fetch_all_categories` fresh on every single query."""
    query_lower = query.strip().lower()
    if not query_lower:
        return None

    exact = [c for c in categories if (c.get("title") or "").strip().lower() == query_lower]
    if exact:
        return CategoryMatch(slug=exact[0].get("slug"), title=exact[0].get("title"), exact=True)

    partial = [c for c in categories if query_lower in (c.get("title") or "").lower()]
    if not partial:
        return None
    partial.sort(key=lambda c: len(c.get("title") or ""))
    best = partial[0]
    return CategoryMatch(slug=best.get("slug"), title=best.get("title"), exact=False)


def resolve_category(client, cart_context: CartContext, query: str) -> CategoryMatch | None:
    """Free-text category name (e.g. "Овочі") -> matched real category, plus
    whether the match was exact. `silpo_get_products`'s own `category` filter
    takes a slug, not a name -- a free-text name there silently matches
    nothing (live-verified). Exact (case-insensitive) title match wins;
    otherwise the shortest title containing the query, on the theory that a
    shorter title is the more specific category (e.g. "Овочі" over "Фрукти,
    овочі"). Issue #05: `exact=False` signals the CLI should warn the user
    which real category it fell back to, since a query like "Вино" can
    otherwise silently match "Виноград" instead."""
    categories = _fetch_all_categories(client, cart_context)
    return resolve_category_from_list(categories, query)


def list_category_titles(client, cart_context: CartContext) -> list[str]:
    """Every real category title currently available, alphabetically --
    powers `deals --list-categories` (issue #05) so a user can see what to
    type for `--category` instead of guessing. Read-only: reuses the same
    `_fetch_all_categories` listing `resolve_category` matches against,
    fetches no deals."""
    categories = _fetch_all_categories(client, cart_context)
    return sorted(title for c in categories if (title := c.get("title")))


def resolve_category_slug(client, cart_context: CartContext, query: str) -> str | None:
    """Thin wrapper over `resolve_category` for callers that only need the
    slug (e.g. `scan_deals`'s category filter)."""
    match = resolve_category(client, cart_context, query)
    return match.slug if match else None


def scan_deals(
    client,
    cart_context: CartContext,
    *,
    limit: int = 10,
    cap_per_category: int = DEFAULT_CATEGORY_CAP,
    category: str | None = None,
) -> list[Deal]:
    if category is not None:
        slug = resolve_category_slug(client, cart_context, category)
        if slug is None:
            raise CategoryNotFoundError(category)

        products_response = (
            client.call(
                "silpo_get_products",
                {
                    "branchId": cart_context.branch_id,
                    "deliveryType": cart_context.delivery_type,
                    "timeslotStart": cart_context.timeslot_start,
                    "timeslotEnd": cart_context.timeslot_end,
                    "category": slug,
                    "mustHavePromotion": True,
                    "sortBy": "promotion",
                    "sortDirection": "desc",
                    "limit": max(limit, cap_per_category),
                },
            )
            or {}
        )
        deals = _deals_from_products(products_response.get("products") or [], cap_per_category)
        deals.sort(key=lambda deal: deal.discount_pct, reverse=True)
        return deals[:limit]

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
        deals.extend(_deals_from_products(products_response.get("products") or [], cap_per_category))

    deals.sort(key=lambda deal: deal.discount_pct, reverse=True)
    return deals[:limit]
