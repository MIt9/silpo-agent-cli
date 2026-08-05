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

No per-id product lookup tool exists in this API (the closest,
`silpo_get_product_details`, needs a `slug`, which nothing here has). Every
replacement candidate -- whether from the interactive free-text search or
the `--replace <old-id> <new-id>` flag -- is therefore resolved the same
way: a `silpo_find_products_batch` free-text search (same pattern
`substitution_resolver.py` already uses, including its fallback of using a
raw product id as the query text when no better text is available), with
plastic-bag candidates (`cart_writer.py`'s `_is_plastic_bag` heuristic)
filtered out of the results before they're ever shown or matched. `cli.py`'s
`--replace` handling searches using the new-product-id itself as the query
and matches a candidate by exact id -- see cli.py's `_run_cart_edit` for the
full reasoning on why this is the flag's chosen semantics.

No-partial-mutation guarantee: `swap_cart_item` validates the old product is
actually a line in the cart (via `CartContext.products`, no extra network
call needed -- same pattern the Cart Writer's non-empty-cart guard uses) and
that the resolved new product record actually has an id, before either the
remove or the add call is made. An old id not in the cart, or a new product
record with no id, raises `CartEditError` and makes zero MCP calls.
"""

from dataclasses import dataclass

from silpo_agent.cart_context import CartContext

_PLASTIC_BAG_KEYWORD = "пакет"


class CartEditError(Exception):
    """A user-facing cart-edit failure (old id not in cart, unresolvable
    replacement). Raised before any mutating MCP call is made."""


@dataclass(frozen=True)
class CartEditResult:
    removed_product_id: str
    added_product_id: str
    added_price: float


def _is_plastic_bag(product: dict) -> bool:
    name = product.get("name") or ""
    return _PLASTIC_BAG_KEYWORD in name.lower()


def _find_cart_product(cart_context: CartContext, product_id: str) -> dict | None:
    return next((p for p in cart_context.products if p.get("productId") == product_id), None)


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


def swap_cart_item(client, cart_context: CartContext, old_product_id: str, new_product: dict) -> CartEditResult:
    """Removes `old_product_id` from the cart and adds `new_product` (a full
    resolved product record -- id/companyId/branchId/price, e.g. from
    `search_replacement_candidates`). Preserves the old line's quantity.
    Raises `CartEditError` -- making zero MCP calls -- if `old_product_id`
    isn't actually in the cart or `new_product` has no id."""
    old_item = _find_cart_product(cart_context, old_product_id)
    if old_item is None:
        raise CartEditError(f"{old_product_id!r} is not in your cart.")

    new_id = new_product.get("id") or new_product.get("productId")
    if not new_id:
        raise CartEditError("Replacement product has no id; cannot add to cart.")

    company_id = new_product.get("companyId") or cart_context.company_id
    branch_id = new_product.get("branchId") or cart_context.branch_id
    quantity = old_item.get("quantity") or 1

    client.call(
        "silpo_remove_cart_products",
        {"shoppingCartId": cart_context.shopping_cart_id, "products": [{"productId": old_product_id}]},
    )
    client.call(
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": cart_context.shopping_cart_id,
            "products": [
                {
                    "productId": new_id,
                    "companyId": company_id,
                    "branchId": branch_id,
                    "quantity": quantity,
                    "addQuantity": True,
                    "comment": None,
                }
            ],
        },
    )

    return CartEditResult(
        removed_product_id=old_product_id, added_product_id=new_id, added_price=new_product.get("price", 0.0)
    )
