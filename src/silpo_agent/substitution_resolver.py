"""Substitution Resolver: for each Typical Item, checks current availability
and, if unavailable, resolves a replacement via `silpo_get_replacements`
(see prd_reorder_optimizer.md's Substitution Resolver section and
CONTEXT.md's "Substitution decision" / "Substitution memory" entries).

Rules:
- Zero replacement candidates: item is reported unavailable, not added to
  the cart, and the run does not crash.
- Exactly one candidate: auto-applied, no prompt.
- More than one candidate: Substitution Memory (`ReorderLogStore.get_substitution`)
  is consulted first for a prior choice; if one exists it's auto-applied
  without asking. Otherwise the user is asked to pick, and the answer is
  persisted via `ReorderLogStore.set_substitution` for future runs.

Runs on the Order Aggregator's output, before the Cart Writer (PRD pipeline:
Address Resolver -> Cart Context Resolver -> Order Aggregator ->
Substitution Resolver -> Cart Writer). Needs the `CartContext` resolved by
#17 (`cart_context.py`) for `branchId`/`companyId`/`deliveryType`/timeslot,
which every product-facing MCP tool below requires.

Real schema (live-verified, see ../../docs/mcp_schema.md's "Product search /
replacement tools" section):
- No `silpo_check_availability` tool exists. Availability is checked via
  `silpo_find_products_batch({"branchId", "deliveryType", "timeslotStart",
  "timeslotEnd", "products": [...], "limit"})` (NOTE: no `companyId` in this
  tool's schema), one batched call across all typical items, using each
  item's `name` as the search query where available (falling back to
  `product_id` for items without one, e.g. substitution/promo results --
  see `_search_query`; `TypicalItem.name` was added in issue #19). Response:
  `{"queries": [{"query", "totalFound", "products": [...]}]}`; a product is
  considered available if the top result for its query has `available: True`
  and `stock > 0` (the real product-record shape:
  `{"id", "name", "slug", "price", "oldPrice", "stock", "available", ...}`).
- `silpo_get_replacements({"branchId", "companyId", "productIds": [...],
  "deliveryType"})` is a genuine BATCH call, one call for all unavailable
  items rather than one per item. Response: `{"success", "summary",
  "items": [...]}`. Tested live against real out-of-stock items and got an
  empty `items` array ("Found replacements for 0 products") -- the populated
  shape of entries inside `items[]` is UNCONFIRMED. This module assumes each
  entry looks like `{"productId": <original id>, "replacements": [<product
  record>, ...]}`, normalizing a single dict to a one-item list the same way
  `address_resolver.py` handles `silpo_find_address`. This is a documented
  guess, not a verified shape -- see docs/mcp_schema.md. Candidate id lookups
  read `"id"` first, falling back to `"productId"`, for the same reason.

If `cart_context.branch_id` is `None` (no cart resolved yet --
`resolve_cart_context` returns an all-None `CartContext` on a first-ever run
or cleared cart, see cart_context.py), both MCP calls are skipped entirely
and every item is reported unavailable rather than sending `None` fields to
the real API. If `branch_id` is present but `company_id` is not (the
address-resolver fallback, issue #29, never has a `company_id` to give --
see cart_context.py), availability checking still runs (`silpo_find_products_batch`
doesn't take a `companyId` at all), but `silpo_get_replacements` is skipped
since it requires one -- unavailable items in that case are reported with no
replacement candidates found, rather than sending `None` as `companyId`.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import CartContext
from silpo_agent.order_aggregator import TypicalItem


@dataclass(frozen=True)
class SubstitutionResult:
    items: list[TypicalItem]
    substitutions: list[tuple[str, str]]
    unavailable: list[str]


def _candidate_id(candidate: dict) -> str | None:
    # "id" matches the general product-record shape used elsewhere; "productId"
    # is a defensive fallback since the populated silpo_get_replacements item
    # shape is still unconfirmed live (see module docstring).
    return candidate.get("id") or candidate.get("productId")


def _to_item(candidate: dict, frequency: float, fallback_price: float, quantity: float) -> TypicalItem:
    return TypicalItem(
        product_id=_candidate_id(candidate),
        frequency=frequency,
        last_known_price=candidate.get("price", fallback_price),
        quantity=quantity,
    )


def _search_query(item: TypicalItem) -> str:
    # A real product name (carried by TypicalItem as of issue #19) is a far
    # better free-text search query than a raw product_id/UUID; fall back to
    # product_id for items that don't have one (e.g. substitution/promo
    # results, which don't carry a name through).
    return item.name or item.product_id


_FIND_PRODUCTS_BATCH_MAX = 30


def _check_availability(client, items: list[TypicalItem], cart_context: CartContext) -> dict[str, bool]:
    """Batched `silpo_get_replacements`-adjacent availability lookup via
    `silpo_find_products_batch`, keyed by each item's search query (see
    `_search_query`). Returns {product_id: is_available}.

    `silpo_find_products_batch` rejects more than 30 products per call
    (real, live-observed limit -- a wide typical-item net, e.g. a low
    --threshold or high --last, can easily exceed it), so queries are
    chunked."""
    if not items:
        return {}

    query_to_ids: dict[str, list[str]] = {}
    for item in items:
        query_to_ids.setdefault(_search_query(item), []).append(item.product_id)

    queries = list(query_to_ids)
    availability: dict[str, bool] = {}
    for i in range(0, len(queries), _FIND_PRODUCTS_BATCH_MAX):
        chunk = queries[i : i + _FIND_PRODUCTS_BATCH_MAX]
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
            query = query_result.get("query")
            products = query_result.get("products") or []
            top = products[0] if products else None
            is_available = bool(top and top.get("available") and (top.get("stock") or 0) > 0)
            for product_id in query_to_ids.get(query, []):
                availability[product_id] = is_available
    return availability


def _fetch_replacements(client, items: list[TypicalItem], cart_context: CartContext) -> dict[str, list[dict]]:
    """One batched `silpo_get_replacements` call for all unavailable items.
    Returns {original_product_id: [candidate_product_record, ...]}."""
    if not items:
        return {}

    response = (
        client.call(
            "silpo_get_replacements",
            {
                "branchId": cart_context.branch_id,
                "companyId": cart_context.company_id,
                "productIds": [item.product_id for item in items],
                "deliveryType": cart_context.delivery_type,
            },
        )
        or {}
    )

    replacements: dict[str, list[dict]] = {}
    for entry in response.get("items") or []:
        product_id = entry.get("productId")
        candidates = entry.get("replacements") or []
        if isinstance(candidates, dict):
            candidates = [candidates]
        replacements[product_id] = candidates
    return replacements


def _resolve_one(
    item: TypicalItem, candidates: list[dict], log_store, input_fn, print_fn
) -> tuple[TypicalItem | None, str | None]:
    """Returns (resolved_item_or_None, substituted_from_product_id_or_None)."""
    if not candidates:
        return None, None

    if len(candidates) == 1:
        return _to_item(candidates[0], item.frequency, item.last_known_price, item.quantity), item.product_id

    remembered_id = log_store.get_substitution(item.product_id)
    if remembered_id is not None:
        match = next((c for c in candidates if _candidate_id(c) == remembered_id), None)
        price = match.get("price", item.last_known_price) if match else item.last_known_price
        return (
            TypicalItem(
                product_id=remembered_id, frequency=item.frequency, last_known_price=price, quantity=item.quantity
            ),
            item.product_id,
        )

    print_fn(f"{item.product_id} is unavailable. Choose a replacement:")
    for i, candidate in enumerate(candidates, start=1):
        print_fn(f"{i}. {_candidate_id(candidate)}")
    choice = input_fn("Pick a number: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(candidates)):
        print_fn(f"No candidate numbered {choice!r}.")
        return None, None

    chosen = candidates[idx - 1]
    log_store.set_substitution(item.product_id, _candidate_id(chosen))
    return _to_item(chosen, item.frequency, item.last_known_price, item.quantity), item.product_id


def resolve_substitutions(
    client,
    log_store,
    typical_items: list[TypicalItem],
    cart_context: CartContext,
    *,
    input_fn=None,
    print_fn=None,
) -> SubstitutionResult:
    input_fn = input_fn or input
    print_fn = print_fn or print

    if not cart_context.branch_id:
        # No cart context resolved yet (first-ever run, or a cleared cart --
        # resolve_cart_context returns an all-None CartContext in that case,
        # see cart_context.py). silpo_find_products_batch requires branchId;
        # sending None would fail against the real API. Skip both lookups and
        # report every item unavailable rather than crashing.
        return SubstitutionResult(
            items=[], substitutions=[], unavailable=[item.product_id for item in typical_items]
        )

    availability = _check_availability(client, typical_items, cart_context)
    unavailable_items = [item for item in typical_items if not availability.get(item.product_id)]
    # silpo_get_replacements requires companyId, unlike silpo_find_products_batch
    # above -- the address-resolver fallback (issue #29) can legitimately
    # produce a branch_id with no company_id (the real
    # silpo_get_available_delivery_types response never carries one), so this
    # lookup is skipped in that case rather than sending a None companyId;
    # affected items simply have no replacement candidates found.
    replacements = _fetch_replacements(client, unavailable_items, cart_context) if cart_context.company_id else {}

    items: list[TypicalItem] = []
    substitutions: list[tuple[str, str]] = []
    unavailable: list[str] = []

    for item in typical_items:
        if availability.get(item.product_id):
            items.append(item)
            continue

        resolved, original_id = _resolve_one(
            item, replacements.get(item.product_id, []), log_store, input_fn, print_fn
        )
        if resolved is None:
            unavailable.append(item.product_id)
        else:
            items.append(resolved)
            substitutions.append((original_id, resolved.product_id))

    return SubstitutionResult(items=items, substitutions=substitutions, unavailable=unavailable)
