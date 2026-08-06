"""Norm Top-Up (ticket 04, part of the smart-cart feature): after typical
items and favorites-deals are assembled, checks each category in the norm
dataset (norm_dataset.py, scaled by --people/--basket-type) against what's
already going into the cart, and proposes an addition for any category with
no real product-id overlap.

Coverage is a real id intersection, not "did we search for the same
product": resolve the category's real product list (`promo_scanner.
resolve_category` + `silpo_get_products(category=slug)`, the same pattern
`deals --category` already uses) and check whether any id already going
into the cart (typical items union favorites-deals, passed in by the
caller) belongs to it. Presence-only "did we search for the same id" was
explicitly rejected in the ticket -- it produces near-permanent false
negatives (a user's usual potato SKU will essentially never equal an
explicit search result's SKU).

`NormItem.category` is an internal English tag (vegetables/fruits/protein/
dairy/grains/pantry/coffee/tea, see norm_dataset.py), not a real Silpo
category title -- `_CATEGORY_SEARCH_QUERY` below maps each tag to a
Ukrainian query fed to `resolve_category`'s exact-then-shortest-substring
matching. Live-verified 2026-08-06 against a real authenticated session via
`silpo-agent deals --list-categories`: 7 of the 8 (vegetables/fruits/
protein/grains/pantry/coffee/tea) are exact matches in the real category
tree. "dairy" has no exact match -- "Молочні продукти" is a substring of two
real titles, "Молочні продукти та яйця" and "Молочні продукти для дітей" --
confirmed live that `resolve_category`'s shortest-title-wins fallback picks
the former, the correct general dairy+eggs category, not a wrong one; the
guess was updated to that exact title so all 8 now resolve via exact match.
A category tag whose guess nonetheless fails to match anything real (e.g.
the live category tree changes) resolves to `None` and is conservatively
treated as uncovered (see `find_uncovered_categories`) rather than silently
skipped.

Norm items found uncovered are searched for by product name (`name_ua`) via
a batched `silpo_find_products_batch` call -- the exact pattern
`substitution_resolver._check_availability` already uses for typical items
(same endpoint, same chunking limit, same {"queries": [{"query",
"totalFound", "products"}]} response shape). Norm items deliberately do NOT
route through `resolve_substitutions`/`silpo_get_replacements`: per ticket
03's review, that machinery's `_to_item`/remembered-substitution branch
silently drops `quantity` (and `company_id`/`branch_id`/`name`) when
constructing a replacement `TypicalItem`, and isn't designed for a one-off
speculative add in the first place. Availability (top result's `available`/
`stock`) is therefore checked right here instead.

Product record shape (docs/mcp_schema.md's "Product search / replacement
tools" section, same shape for `silpo_get_products`/`silpo_find_products_
batch`/`silpo_get_similar_products`): `{"id", "name", "slug", "price",
"oldPrice", "stock", "available", "weighted", "step", "ratio", ...}` -- the
step field for a *search-result* product record is `step`, not
`addToBasketStep` (that spelling only appears in
`silpo_get_shopping_cart_by_id`'s own already-in-cart product record, a
different endpoint). Read `step` first, falling back to `addToBasketStep`
defensively in case a given category ever returns the other shape.

`weighted` does NOT reliably indicate what unit `step`/quantity is measured
in -- live-verified 2026-08-06: "Оселедець під шубою" (a prepared salad)
has `weighted: false` yet `ratio: "кг"`, `step: 0.25` (buyable in 0.25kg
increments), while "Крупа «Сквирянка» гречана" (a packaged buckwheat) also
has `weighted: false` but `ratio: "шт"`, `step: 1` (whole packs only, no
kg-per-pack figure anywhere on the record or in its `attributes`). `ratio`,
not `weighted`, is the real signal for whether a kg/l norm target converts
to this product's quantity at all -- see `_NORM_UNIT_TO_RATIO` and
`resolve_norm_top_up_items`'s docstring.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import CartContext
from silpo_agent.cart_writer import round_to_step
from silpo_agent.norm_dataset import NormItem
from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.promo_scanner import _fetch_all_categories, resolve_category_from_list

_FIND_PRODUCTS_BATCH_MAX = 30

# NormItem.unit ("kg"/"l") -> the real product record's `ratio` field
# (docs/mcp_schema.md's undocumented-but-live-observed unit label, e.g.
# "кг"/"л"/"шт") when they mean the same unit. A norm target expressed in
# kg/l is only directly usable as a cart quantity when the matched product
# is ALSO sold in that same unit -- see resolve_norm_top_up_items's
# docstring for why a "шт" (piece/pack) product can't be converted.
_NORM_UNIT_TO_RATIO = {"kg": "кг", "l": "л"}

# silpo_get_products's `limit` hard-rejects anything over 100 (confirmed
# live: "Too big: expected number to be <=100"). Some categories list
# thousands of products (promo_scanner.py's DEFAULT_CATEGORY_CAP docstring),
# so a coverage check against only the first 100 can still miss a real match
# -- same accepted tradeoff promo_scanner.py's own category cap already
# makes, not new risk introduced here.
_CATEGORY_PRODUCTS_CAP = 100

# Ukrainian query per norm_dataset.py category tag, fed through
# resolve_category's exact-then-shortest-substring matching. Live-verified
# 2026-08-06 -- see module docstring.
_CATEGORY_SEARCH_QUERY = {
    "vegetables": "Овочі",
    "fruits": "Фрукти",
    "protein": "М'ясо",
    "dairy": "Молочні продукти та яйця",
    "grains": "Крупи",
    "pantry": "Бакалія",
    "coffee": "Кава",
    "tea": "Чай",
}


def find_uncovered_categories(
    client,
    cart_context: CartContext,
    norms: list[NormItem],
    existing_product_ids: set[str],
    *,
    cap: int = _CATEGORY_PRODUCTS_CAP,
    print_fn=None,
) -> list[NormItem]:
    """Norm items whose category has no real id overlap with
    `existing_product_ids` (typical items union favorites-deals). A category
    tag that fails to resolve to a real category (see `_CATEGORY_SEARCH_QUERY`
    -- live-verified 2026-08-06, but the live category tree can still change)
    is treated as uncovered -- we have no real product list to check
    coverage against, so err on proposing rather than silently dropping it
    -- and a warning is printed so a future mismatch doesn't fail silently on
    every single run.

    Fetches the (paginated) category tree once via `_fetch_all_categories`
    and matches all of `norms`'s distinct category tags against that one
    in-memory list (`promo_scanner.resolve_category_from_list`), rather than
    re-fetching the whole tree per category tag -- up to 6x fewer round
    trips per smart-cart run.
    """
    print_fn = print_fn or print
    categories = sorted({item.category for item in norms})
    all_categories = _fetch_all_categories(client, cart_context)
    uncovered: set[str] = set()

    for category in categories:
        query = _CATEGORY_SEARCH_QUERY.get(category, category)
        match = resolve_category_from_list(all_categories, query)
        if match is None:
            print_fn(
                f"norm top-up: could not resolve category {category!r} (guessed {query!r}); "
                "treating as uncovered."
            )
            uncovered.add(category)
            continue

        response = (
            client.call(
                "silpo_get_products",
                {
                    "branchId": cart_context.branch_id,
                    "deliveryType": cart_context.delivery_type,
                    "timeslotStart": cart_context.timeslot_start,
                    "timeslotEnd": cart_context.timeslot_end,
                    "category": match.slug,
                    "limit": cap,
                },
            )
            or {}
        )
        category_ids = {p.get("id") for p in response.get("products") or []}
        if category_ids.isdisjoint(existing_product_ids):
            uncovered.add(category)

    return [item for item in norms if item.category in uncovered]


@dataclass(frozen=True)
class NormTopUpResult:
    items: list[TypicalItem]
    skipped: list[str]  # name_ua of norm items skipped (no/unavailable result)
    # The proposed TypicalItem instances (not bare product ids -- two
    # different norm items resolving to the same top search result, one
    # seasonal and one not, would misattribute a plain id-based flag; their
    # `quantity` differs in that case since it comes from each norm's own
    # quantity_per_person) whose source NormItem has a seasonal profile
    # (norm_dataset.py, ticket 06) that's out of season for the `month` this
    # call was given -- purely informational (see resolve_norm_top_up_items's
    # docstring), the caller (cli.py) prints a note next to these, it does
    # not change `quantity` here (that's already been scaled upstream by
    # norm_dataset.get_norms).
    out_of_season_items: frozenset[TypicalItem] = frozenset()


def resolve_norm_top_up_items(
    client,
    cart_context: CartContext,
    norm_items: list[NormItem],
    people: int,
    *,
    month: int | None = None,
    print_fn=None,
) -> NormTopUpResult:
    """Searches each uncovered norm item by name (batched), skips anything
    with zero results or an unavailable top result, and computes the cart
    quantity for the rest.

    The norm's `quantity_per_person * people` target is in kg/l -- only
    usable as-is when the matched product's own `ratio` field says it's
    sold in that same unit (`_NORM_UNIT_TO_RATIO`). When it isn't (a "шт"
    piece/pack product, or `ratio` missing/unrecognized), there's no way to
    convert a kg/l target into a pack count without the pack's real net
    weight, which the API doesn't expose anywhere -- rather than fabricate
    a number (silently ceiling the kg figure as if it were a pack count,
    live-observed to under- or over-buy depending on the real pack size),
    this falls back to quantity 1 with a printed note so the mismatch is
    visible, not silently wrong.

    `month` (ticket 06) is used only to flag which proposed items came from
    an out-of-season seasonal NormItem (see `NormTopUpResult.
    out_of_season_items`) -- `quantity_per_person` has already been scaled
    for seasonality upstream by `norm_dataset.get_norms(month=...)`, so
    `month` here never touches the quantity math, only the note flag."""
    print_fn = print_fn or print

    if not norm_items:
        return NormTopUpResult(items=[], skipped=[])

    query_names = list({item.name_ua for item in norm_items})
    top_by_query: dict[str, dict | None] = {}
    for i in range(0, len(query_names), _FIND_PRODUCTS_BATCH_MAX):
        chunk = query_names[i : i + _FIND_PRODUCTS_BATCH_MAX]
        response = (
            client.call(
                "silpo_find_products_batch",
                {
                    "branchId": cart_context.branch_id,
                    "deliveryType": cart_context.delivery_type,
                    "timeslotStart": cart_context.timeslot_start,
                    "timeslotEnd": cart_context.timeslot_end,
                    "products": chunk,
                    "limit": 1,
                },
            )
            or {}
        )
        for query_result in response.get("queries") or []:
            products = query_result.get("products") or []
            top_by_query[query_result.get("query")] = products[0] if products else None

    items: list[TypicalItem] = []
    skipped: list[str] = []
    out_of_season_items: set[TypicalItem] = set()
    for norm_item in norm_items:
        product = top_by_query.get(norm_item.name_ua)
        is_available = bool(product and product.get("available") and (product.get("stock") or 0) > 0)
        if not is_available:
            print_fn(f"norm top-up: no available product found for {norm_item.name_ua!r}; skipping.")
            skipped.append(norm_item.name_ua)
            continue

        raw_quantity = norm_item.quantity_per_person * people
        expected_ratio = _NORM_UNIT_TO_RATIO.get(norm_item.unit)
        product_ratio = product.get("ratio")
        if expected_ratio is not None and product_ratio == expected_ratio:
            step = product.get("step")
            if step is None:
                step = product.get("addToBasketStep")
            quantity = round_to_step(raw_quantity, step=step)
        else:
            print_fn(
                f"norm top-up: {norm_item.name_ua!r} needs {raw_quantity:.2f}{norm_item.unit}, but its "
                f"product is sold by {product_ratio or 'an unknown unit'} -- can't convert to a pack count, "
                "added 1 (adjust the quantity manually if needed)."
            )
            quantity = 1.0

        item = TypicalItem(
            product_id=product.get("id"),
            frequency=0.0,
            last_known_price=product.get("price"),
            company_id=product.get("companyId"),
            branch_id=product.get("branchId"),
            name=product.get("name"),
            quantity=quantity,
        )
        # Keyed by the resolved TypicalItem itself (not a bare product_id)
        # so two different norm items that happen to resolve to the same
        # top search result -- one seasonal, one not -- can't misattribute
        # the note onto the wrong one; their `quantity` (from each norm's
        # own quantity_per_person) almost always differs even when the
        # product_id coincides.
        if month is not None and norm_item.in_season_months is not None and month not in norm_item.in_season_months:
            out_of_season_items.add(item)
        items.append(item)

    return NormTopUpResult(items=items, skipped=skipped, out_of_season_items=frozenset(out_of_season_items))
