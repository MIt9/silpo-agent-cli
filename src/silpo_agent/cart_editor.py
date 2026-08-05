"""Cart Editor: manually swap one cart item for another (see prd_reorder_
optimizer.md's Cart Editor section / issue #30). The only capability in this
project besides `reorder` itself that mutates the real cart outside the
reorder pipeline.

Real schema (see docs/mcp_schema.md's "Cart tools" section, live-verified):
- `silpo_remove_cart_products({"shoppingCartId", "products": [{"productId"}]})`
- `silpo_add_or_update_cart_products` -- same real payload shape
  `cart_writer.py` already established: `{"shoppingCartId", "products":
  [{"productId", "companyId", "branchId", "quantity", "addQuantity",
  "comment"}]}`. There is no in-place "update this line" call -- remove-then-
  add is the only way to change a cart item.

Products are addressed by **slug**, not by product id (issue #50) -- see
`resolve_product_by_slug` below and docs/mcp_schema.md's issue #50 section.
Two resolution paths exist, for two different situations:

- `--replace <old-slug> <new-slug>` resolves the new product with
  `silpo_get_product_details`, a real per-slug lookup. Deterministic, and it
  returns `companyId`/`branchId` on the record itself.
- the interactive flow's free-text search still goes through
  `silpo_find_products_batch` (same pattern `substitution_resolver.py`
  uses), because there the user types words, not a slug. Plastic-bag
  candidates (`cart_writer.py`'s `_is_plastic_bag` heuristic) are filtered
  out of those results before they're ever shown.

No-partial-mutation guarantee: `swap_cart_item` validates the old slug is
actually a line in the cart (via `CartContext.products`, no extra network
call needed -- same pattern the Cart Writer's non-empty-cart guard uses) and
that the resolved new product record actually has an id, before either the
remove or the add call is made. An old slug not in the cart, or a new product
record with no id, raises `CartEditError` and makes zero MCP calls.

Failure *between* the two calls (remove succeeds, add doesn't) is a second,
distinct risk the above guarantee doesn't cover -- a bare remove-then-add
with no error handling would silently drop the old item on the floor if the
add call blows up. `auth.py`'s `MCPClient.call()` signals a failed tool call
by raising `MCPError` (or, for a lower-level transport problem, some other
exception) rather than returning an error payload -- see `auth.py`'s
`call_tool_http`/`_unwrap_tool_result`. `swap_cart_item` catches exactly
that: if the add call raises anything, it attempts one best-effort re-add of
the old item (same productId/companyId/branchId/quantity just removed) as a
rollback, then always raises `CartEditError` either way -- worded
differently depending on whether the rollback itself worked -- rather than
ever returning normally with the cart left in a state the caller doesn't
know about.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import CartContext

_PLASTIC_BAG_KEYWORD = "пакет"


class CartEditError(Exception):
    """A user-facing cart-edit failure (old id not in cart, unresolvable
    replacement). Raised before any mutating MCP call is made."""


@dataclass(frozen=True)
class CartEditResult:
    # Issue #50: reported in slugs, not UUIDs -- slug is what this CLI
    # publishes and what `--replace` takes, so it's what the user sees back.
    # removed_slug is None for a pure add (--add): nothing was removed.
    removed_slug: str | None
    added_slug: str | None
    added_price: float
    # Only set by remove_cart_item (a pure delete has no added_price to
    # report); defaults to 0.0 so swap/add's existing keyword-only
    # construction sites are unaffected.
    removed_price: float = 0.0


def _is_plastic_bag(product: dict) -> bool:
    name = product.get("name") or ""
    return _PLASTIC_BAG_KEYWORD in name.lower()


def _find_cart_product(cart_context: CartContext, slug: str) -> dict | None:
    """Cart lines are addressed by slug (issue #50). Matched locally against
    `CartContext.products` -- no network call, same as when this matched on
    `productId`."""
    return next((p for p in cart_context.products if p.get("slug") == slug), None)


def search_replacement_candidates(client, cart_context: CartContext, query: str) -> list[dict]:
    """Free-text product search via `silpo_find_products_batch` (same real
    call shape `substitution_resolver.py`'s `_check_availability` already
    uses), plastic-bag candidates filtered out before returning."""
    response = (
        client.call(
            "silpo_find_products_batch",
            {
                "branchId": cart_context.branch_id,
                "deliveryType": cart_context.delivery_type,
                "timeslotStart": cart_context.timeslot_start,
                "timeslotEnd": cart_context.timeslot_end,
                "products": [query],
                "limit": 10,
            },
        )
        or {}
    )
    queries = response.get("queries") or []
    query_result = next((q for q in queries if q.get("query") == query), queries[0] if queries else {})
    candidates = query_result.get("products") or []
    return [candidate for candidate in candidates if not _is_plastic_bag(candidate)]


def resolve_product_by_slug(client, cart_context: CartContext, slug: str) -> dict | None:
    """Resolves a slug to a full product record via `silpo_get_product_details`
    (issue #50). Returns None if the slug doesn't resolve.

    Real live-verified request/response (docs/mcp_schema.md): the call takes
    `{branchId, slug, deliveryType, timeslotStart, timeslotEnd}` and answers
    `{"success", "product": {"id", "name", "slug", "price", "oldPrice",
    "stock", "available", "companyId", "branchId", ...}}` -- note the
    `"product"` wrapper, and that `companyId`/`branchId` come back on the
    record itself. That last part matters: it means the replacement path
    never has to fall back to `CartContext.company_id`, which is `None` on
    the issue #29 no-shipments path.

    This replaces the previous resolution path, which used a product id as a
    free-text `silpo_find_products_batch` query and matched a candidate by
    exact id -- indirect, and unreliable for exactly the reason issue #18
    documents (a raw UUID as search text usually returns nothing)."""
    response = (
        client.call(
            "silpo_get_product_details",
            {
                "branchId": cart_context.branch_id,
                "slug": slug,
                "deliveryType": cart_context.delivery_type,
                "timeslotStart": cart_context.timeslot_start,
                "timeslotEnd": cart_context.timeslot_end,
            },
        )
        or {}
    )
    return response.get("product") or None


def _add_call(client, cart_context: CartContext, product_id: str, company_id, branch_id, quantity):
    return client.call(
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": cart_context.shopping_cart_id,
            "products": [
                {
                    "productId": product_id,
                    "companyId": company_id,
                    "branchId": branch_id,
                    "quantity": quantity,
                    "addQuantity": True,
                }
            ],
        },
    )


def swap_cart_item(client, cart_context: CartContext, old_slug: str, new_product: dict) -> CartEditResult:
    """Removes the cart line whose slug is `old_slug` and adds `new_product`
    (a full resolved product record -- id/companyId/branchId/price, e.g.
    from `resolve_product_by_slug`). Preserves the old line's quantity.
    Raises `CartEditError` -- making zero MCP calls -- if `old_slug` isn't
    actually in the cart or `new_product` has no id.

    Slugs in, slugs out (issue #50): the product UUIDs the MCP calls need are
    read off the matched cart line and off `new_product`, never asked of the
    caller.

    If the add call fails after the remove already succeeded, attempts a
    best-effort rollback (re-adding the old item) and always raises
    `CartEditError` describing what happened -- see module docstring."""
    # A missing slug must never reach the match: `_find_cart_product`
    # compares `p.get("slug") == slug`, so `None` would match the first
    # slug-less cart line and silently swap the wrong item. Guarded here
    # rather than in each caller -- both the interactive flow and
    # `--replace` route through this function.
    if not old_slug:
        raise CartEditError("That cart item has no slug, so it can't be addressed for replacement.")

    old_item = _find_cart_product(cart_context, old_slug)
    if old_item is None:
        raise CartEditError(f"{old_slug!r} is not in your cart.")

    old_product_id = old_item.get("productId")
    new_id = new_product.get("id") or new_product.get("productId")
    if not new_id:
        raise CartEditError("Replacement product has no id; cannot add to cart.")

    new_company_id = new_product.get("companyId") or cart_context.company_id
    new_branch_id = new_product.get("branchId") or cart_context.branch_id
    old_company_id = old_item.get("companyId") or cart_context.company_id
    old_branch_id = old_item.get("branchId") or cart_context.branch_id
    quantity = old_item.get("quantity") or 1

    client.call(
        "silpo_remove_cart_products",
        {"shoppingCartId": cart_context.shopping_cart_id, "products": [{"productId": old_product_id}]},
    )

    try:
        _add_call(client, cart_context, new_id, new_company_id, new_branch_id, quantity)
    except Exception as add_exc:  # noqa: BLE001 -- deliberately broad: a raw
        # transport failure (network error) surfaces as a plain exception
        # from client.call(), not just MCPError (see auth.py's call_tool_http
        # -- _post_json isn't wrapped), and both must trigger the rollback.
        _attempt_rollback_and_raise(
            client, cart_context, old_slug, old_product_id, old_company_id, old_branch_id, quantity, new_id, add_exc
        )

    return CartEditResult(
        removed_slug=old_slug,
        added_slug=new_product.get("slug"),
        added_price=new_product.get("price", 0.0),
    )


def add_cart_item(client, cart_context: CartContext, new_product: dict, quantity: int = 1) -> CartEditResult:
    """Adds `new_product` (a full resolved product record, e.g. from
    `resolve_product_by_slug`) as a brand-new cart line. No removal
    involved, so unlike `swap_cart_item` there's no partial-mutation
    rollback case to handle.

    Raises `CartEditError` -- making zero MCP calls -- if the product has no
    id, or if its productId already matches a line already in the cart:
    reorder's `addQuantity=True` blindly re-adding an already-present item
    silently doubled its quantity (see cart_writer.py's dedupe fix), and
    --add must not repeat that mistake. Point the user at --replace or
    reorder instead, rather than guessing whether they wanted +N."""
    new_id = new_product.get("id") or new_product.get("productId")
    if not new_id:
        raise CartEditError("Product has no id; cannot add to cart.")

    if any(p.get("productId") == new_id for p in cart_context.products):
        label = new_product.get("slug") or new_id
        raise CartEditError(
            f"{label!r} is already in your cart; use `cart edit --replace` to swap it "
            "or `reorder` to restock it, rather than adding a second line."
        )

    new_company_id = new_product.get("companyId") or cart_context.company_id
    new_branch_id = new_product.get("branchId") or cart_context.branch_id

    _add_call(client, cart_context, new_id, new_company_id, new_branch_id, quantity)

    return CartEditResult(
        removed_slug=None,
        added_slug=new_product.get("slug"),
        added_price=new_product.get("price", 0.0),
    )


def remove_cart_item(client, cart_context: CartContext, slug: str) -> CartEditResult:
    """Removes the cart line whose slug is `slug`, adding nothing back --
    the delete-only counterpart to `swap_cart_item`. Raises `CartEditError`
    -- making zero MCP calls -- if `slug` isn't actually a line in the cart,
    same guard `swap_cart_item` applies to its old-slug argument."""
    item = _find_cart_product(cart_context, slug)
    if item is None:
        raise CartEditError(f"{slug!r} is not in your cart.")

    client.call(
        "silpo_remove_cart_products",
        {"shoppingCartId": cart_context.shopping_cart_id, "products": [{"productId": item.get("productId")}]},
    )

    return CartEditResult(
        removed_slug=slug, added_slug=None, added_price=0.0, removed_price=item.get("price", 0.0)
    )


def _attempt_rollback_and_raise(
    client, cart_context, old_slug, old_product_id, old_company_id, old_branch_id, quantity, new_id, add_exc
):
    """Removed the `old_slug` line but the add of `new_id` blew up
    (`add_exc`). Best-effort re-add of the old item, then always raise -- the
    caller must never see a normal return with the cart silently missing an
    item. Messages name the slug, the identifier the user actually holds."""
    try:
        _add_call(client, cart_context, old_product_id, old_company_id, old_branch_id, quantity)
    except Exception:  # noqa: BLE001 -- same broad-catch reasoning as above
        raise CartEditError(
            f"Removed {old_slug!r}, failed to add {new_id!r} ({add_exc}), and failed to restore "
            f"{old_slug!r} -- your cart may be missing an item, please check manually."
        ) from add_exc

    raise CartEditError(
        f"Removed {old_slug!r} but failed to add {new_id!r} ({add_exc}); restored {old_slug!r} to your cart."
    ) from add_exc
