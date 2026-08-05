from silpo_agent.auth import MCPError
from silpo_agent.cart_context import CartContext
from silpo_agent.cart_editor import (
    CartEditError,
    add_cart_item,
    resolve_product_by_slug,
    search_replacement_candidates,
    set_cart_item_quantity,
    swap_cart_item,
)


class FakeClient:
    def __init__(self, responses: dict | None = None):
        self.calls = []
        self._responses = responses or {}

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        response = self._responses.get(tool)
        return response(args) if callable(response) else response


def cart_context(products=None, shopping_cart_id="cart-1", branch_id="b1", company_id="c1"):
    return CartContext(
        shopping_cart_id=shopping_cart_id,
        branch_id=branch_id,
        company_id=company_id,
        delivery_type="DeliveryHome",
        timeslot_start="2026-08-04T10:00:00",
        timeslot_end="2026-08-04T12:00:00",
        validations=[],
        products=products or [],
    )


def _batch_response(query, products):
    return {"success": True, "queries": [{"query": query, "totalFound": len(products), "products": products}]}


# --- resolve_product_by_slug -----------------------------------------


def _product_details_response(slug):
    """Real live-verified `silpo_get_product_details` response (issue #50,
    see docs/mcp_schema.md) -- note the `{"product": {...}}` wrapper, and
    that `companyId`/`branchId` come back on the record itself."""
    return {
        "success": True,
        "product": {
            "id": "1ed07691-5224-68b2-99ed-dd63763181f9",
            "name": "Ряжанка «Ферма» 3,2%",
            "slug": slug,
            "price": 66.9,
            "oldPrice": 88.49,
            "stock": 6,
            "available": True,
            "companyId": "1ec88c5d-a050-669c-8467-570a157f3e31",
            "branchId": "1ee56cbf-071e-6c1c-a437-67a8740f8c75",
        },
    }


def test_resolve_product_by_slug_uses_the_real_details_call_not_a_text_search():
    """Issue #50: replacements resolve through `silpo_get_product_details`,
    a deterministic per-slug lookup -- not the old indirect path of using an
    id as a free-text search query and matching by exact id (issue #18)."""
    context = cart_context()
    client = FakeClient({"silpo_get_product_details": _product_details_response("riazhanka-ferma-3-2-837453")})

    product = resolve_product_by_slug(client, context, "riazhanka-ferma-3-2-837453")

    assert product["id"] == "1ed07691-5224-68b2-99ed-dd63763181f9"
    assert product["companyId"] == "1ec88c5d-a050-669c-8467-570a157f3e31"
    assert (
        "silpo_get_product_details",
        {
            "branchId": "b1",
            "slug": "riazhanka-ferma-3-2-837453",
            "deliveryType": "DeliveryHome",
            "timeslotStart": "2026-08-04T10:00:00",
            "timeslotEnd": "2026-08-04T12:00:00",
        },
    ) in client.calls
    assert all(call[0] != "silpo_find_products_batch" for call in client.calls)


def test_resolve_product_by_slug_returns_none_for_an_unknown_slug():
    context = cart_context()
    client = FakeClient({"silpo_get_product_details": {"success": True}})

    assert resolve_product_by_slug(client, context, "no-such-slug") is None


# --- swap_cart_item ---------------------------------------------------


def test_swap_cart_item_removes_old_and_adds_new():
    context = cart_context(products=[{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity":1}])
    client = FakeClient()
    new_product = {"id": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0, "name": "Oat Milk"}

    result = swap_cart_item(client, context, "milk", new_product)

    assert result.removed_slug == "milk"
    assert result.added_price == 55.0
    assert (
        "silpo_remove_cart_products",
        {"shoppingCartId": "cart-1", "products": [{"productId": "milk"}]},
    ) in client.calls
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "oat-milk",
                    "companyId": "c2",
                    "branchId": "b2",
                    "quantity": 1,
                    "addQuantity": True,
                }
            ],
        },
    ) in client.calls
    # remove must happen before add
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_remove_cart_products") < tool_order.index("silpo_add_or_update_cart_products")


def test_swap_cart_item_takes_a_slug_and_removes_by_the_lines_product_id():
    """Issue #50: slug is the CLI's public product identifier, so the old
    item is named by slug. The UUID never leaves the module -- it's read off
    the matched cart line for the `silpo_remove_cart_products` call."""
    context = cart_context(
        products=[
            {
                "productId": "1ed0762e-b3f9-6ca8-bdbf-dd63763181f9",
                "slug": "moloko-ferma-ultrapasteryzovane-2-5-576829",
                "companyId": "c1",
                "branchId": "b1",
                "quantity": 1,
            }
        ]
    )
    client = FakeClient()
    new_product = {"id": "oat-milk-uuid", "slug": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    result = swap_cart_item(client, context, "moloko-ferma-ultrapasteryzovane-2-5-576829", new_product)

    assert result.removed_slug == "moloko-ferma-ultrapasteryzovane-2-5-576829"
    assert result.added_slug == "oat-milk"
    assert (
        "silpo_remove_cart_products",
        {"shoppingCartId": "cart-1", "products": [{"productId": "1ed0762e-b3f9-6ca8-bdbf-dd63763181f9"}]},
    ) in client.calls


def test_swap_cart_item_refuses_a_missing_slug_instead_of_matching_a_slugless_line():
    """A cart line without a slug can't be addressed. Without this guard
    `_find_cart_product` would match `p.get("slug") == None` and silently
    swap whichever slug-less line happens to come first -- the wrong item."""
    context = cart_context(
        products=[
            {"productId": "no-slug-1", "companyId": "c1", "branchId": "b1", "quantity": 1},
            {"productId": "no-slug-2", "companyId": "c1", "branchId": "b1", "quantity": 1},
        ]
    )
    client = FakeClient()

    try:
        swap_cart_item(client, context, None, {"id": "x", "slug": "x", "price": 1.0})
        assert False, "expected CartEditError"
    except CartEditError as exc:
        assert "slug" in str(exc).lower()

    assert client.calls == []


def test_swap_cart_item_unknown_slug_raises_before_any_call():
    context = cart_context(products=[{"productId": "p1", "slug": "bread", "companyId": "c1", "quantity": 1}])
    client = FakeClient()

    try:
        swap_cart_item(client, context, "no-such-slug", {"id": "x", "slug": "x", "price": 1.0})
        assert False, "expected CartEditError"
    except CartEditError as exc:
        assert "no-such-slug" in str(exc)

    assert client.calls == []


def test_swap_cart_item_preserves_old_quantity():
    context = cart_context(products=[{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity":3}])
    client = FakeClient()
    new_product = {"id": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    swap_cart_item(client, context, "milk", new_product)

    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["quantity"] == 3


def test_swap_cart_item_falls_back_to_cart_context_branch_and_company():
    context = cart_context(
        products=[{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity":1}],
        branch_id="ctx-b",
        company_id="ctx-c",
    )
    client = FakeClient()
    new_product = {"id": "oat-milk", "price": 55.0}

    swap_cart_item(client, context, "milk", new_product)

    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["companyId"] == "ctx-c"
    assert added_call[1]["products"][0]["branchId"] == "ctx-b"


def test_swap_cart_item_old_id_not_in_cart_raises_and_makes_no_calls():
    context = cart_context(products=[{"productId": "bread", "companyId": "c1", "branchId": "b1", "quantity": 1}])
    client = FakeClient()
    new_product = {"id": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    try:
        swap_cart_item(client, context, "milk", new_product)
        assert False, "expected CartEditError"
    except CartEditError as exc:
        assert "milk" in str(exc)

    assert client.calls == []


def test_swap_cart_item_add_failure_after_remove_rolls_back_and_raises_clear_error():
    """If silpo_remove_cart_products succeeds but the following
    silpo_add_or_update_cart_products fails, the old item must not simply
    vanish -- swap_cart_item attempts a best-effort re-add of the old item
    (same productId/companyId/branchId/quantity just removed) before
    raising, and the error message says what happened."""
    context = cart_context(products=[{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity":2}])

    def add_response(args):
        if args["products"][0]["productId"] == "oat-milk":
            raise MCPError("add failed")
        return {"success": True}

    client = FakeClient(
        {"silpo_remove_cart_products": {"success": True}, "silpo_add_or_update_cart_products": add_response}
    )
    new_product = {"id": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    try:
        swap_cart_item(client, context, "milk", new_product)
        assert False, "expected CartEditError"
    except CartEditError as exc:
        message = str(exc).lower()
        assert "milk" in message
        assert "restored" in message or "restore" in message

    add_calls = [c for c in client.calls if c[0] == "silpo_add_or_update_cart_products"]
    assert len(add_calls) == 2
    assert add_calls[0][1]["products"][0]["productId"] == "oat-milk"
    # rollback re-adds the OLD item with its original line's context/quantity
    rollback_product = add_calls[1][1]["products"][0]
    assert rollback_product["productId"] == "milk"
    assert rollback_product["companyId"] == "c1"
    assert rollback_product["branchId"] == "b1"
    assert rollback_product["quantity"] == 2


def test_swap_cart_item_add_and_rollback_both_fail_reports_inconsistent_state():
    context = cart_context(products=[{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity":1}])

    def add_response(args):
        raise MCPError("add failed")

    client = FakeClient(
        {"silpo_remove_cart_products": {"success": True}, "silpo_add_or_update_cart_products": add_response}
    )
    new_product = {"id": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    try:
        swap_cart_item(client, context, "milk", new_product)
        assert False, "expected CartEditError"
    except CartEditError as exc:
        message = str(exc).lower()
        assert "milk" in message
        assert "manually" in message or "may be missing" in message

    # rollback was still attempted even though it too failed
    add_calls = [c for c in client.calls if c[0] == "silpo_add_or_update_cart_products"]
    assert len(add_calls) == 2


def test_swap_cart_item_new_product_without_id_raises_and_makes_no_calls():
    context = cart_context(products=[{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity":1}])
    client = FakeClient()

    try:
        swap_cart_item(client, context, "milk", {"price": 10.0})
        assert False, "expected CartEditError"
    except CartEditError:
        pass

    assert client.calls == []


# --- add_cart_item -----------------------------------------------------


def test_add_cart_item_adds_a_new_line_quantity_one():
    context = cart_context(products=[{"productId": "milk", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}])
    client = FakeClient()
    new_product = {"id": "oat-milk", "slug": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    result = add_cart_item(client, context, new_product)

    assert result.removed_slug is None
    assert result.added_slug == "oat-milk"
    assert result.added_price == 55.0
    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {"productId": "oat-milk", "companyId": "c2", "branchId": "b2", "quantity": 1, "addQuantity": True}
            ],
        },
    ) in client.calls
    assert all(call[0] != "silpo_remove_cart_products" for call in client.calls)


def test_add_cart_item_uses_the_given_quantity():
    context = cart_context()
    client = FakeClient()
    new_product = {"id": "oat-milk", "slug": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    add_cart_item(client, context, new_product, quantity=2)

    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["quantity"] == 2


def test_add_cart_item_falls_back_to_cart_context_branch_and_company():
    context = cart_context(branch_id="ctx-b", company_id="ctx-c")
    client = FakeClient()
    new_product = {"id": "oat-milk", "price": 55.0}

    add_cart_item(client, context, new_product)

    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["companyId"] == "ctx-c"
    assert added_call[1]["products"][0]["branchId"] == "ctx-b"


def test_add_cart_item_already_in_cart_raises_and_makes_no_calls():
    context = cart_context(products=[{"productId": "oat-milk", "slug": "oat-milk", "companyId": "c1", "branchId": "b1", "quantity": 1}])
    client = FakeClient()
    new_product = {"id": "oat-milk", "slug": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    try:
        add_cart_item(client, context, new_product)
        assert False, "expected CartEditError"
    except CartEditError as exc:
        assert "oat-milk" in str(exc)
        assert "already" in str(exc).lower()

    assert client.calls == []


def test_add_cart_item_without_id_raises_and_makes_no_calls():
    context = cart_context()
    client = FakeClient()

    try:
        add_cart_item(client, context, {"price": 10.0})
        assert False, "expected CartEditError"
    except CartEditError:
        pass

    assert client.calls == []


# --- set_cart_item_quantity --------------------------------------------


def test_set_cart_item_quantity_unknown_slug_raises_and_makes_no_calls():
    context = cart_context(products=[{"productId": "bread", "slug": "bread", "companyId": "c1", "quantity": 1}])
    client = FakeClient()

    try:
        set_cart_item_quantity(client, context, "milk", 3)
        assert False, "expected CartEditError"
    except CartEditError as exc:
        assert "milk" in str(exc)

    assert client.calls == []


def test_set_cart_item_quantity_sets_the_absolute_value_not_a_delta():
    """`addQuantity: True` adds to the existing line quantity (per
    cart_writer.py's own doc note) -- --qty needs "set to N", the opposite,
    so this must send `addQuantity: False`."""
    context = cart_context(
        products=[
            {"productId": "milk-id", "slug": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1, "price": 49.9}
        ]
    )
    client = FakeClient()

    result = set_cart_item_quantity(client, context, "milk", 3)

    assert (
        "silpo_add_or_update_cart_products",
        {
            "shoppingCartId": "cart-1",
            "products": [
                {
                    "productId": "milk-id",
                    "companyId": "c1",
                    "branchId": "b1",
                    "quantity": 3,
                    "addQuantity": False,
                }
            ],
        },
    ) in client.calls
    assert result.old_quantity == 1
    assert result.new_quantity == 3
    assert result.added_price == 49.9
    assert result.added_slug == "milk"
    assert result.removed_slug is None


def test_set_cart_item_quantity_accepts_fractional_values_for_weighted_items():
    """Issue 01 (blocker): weighted items (meat, produce sold by kg) need
    quantities like 0.5 or 1.5, not just whole packs."""
    context = cart_context(
        products=[
            {"productId": "gulyash-id", "slug": "gulyash", "companyId": "c1", "branchId": "b1", "quantity": 1.5, "price": 320.0}
        ]
    )
    client = FakeClient()

    result = set_cart_item_quantity(client, context, "gulyash", 0.5)

    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["quantity"] == 0.5
    assert result.old_quantity == 1.5
    assert result.new_quantity == 0.5


# --- search_replacement_candidates -------------------------------------


def test_search_replacement_candidates_returns_search_results():
    context = cart_context()
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch_response(
                "oat milk", [{"id": "oat-milk", "name": "Oat Milk", "price": 55.0}]
            )
        }
    )

    candidates = search_replacement_candidates(client, context, "oat milk")

    assert candidates == [{"id": "oat-milk", "name": "Oat Milk", "price": 55.0}]
    find_call = next(c for c in client.calls if c[0] == "silpo_find_products_batch")
    assert find_call[1] == {
        "branchId": "b1",
        "deliveryType": "DeliveryHome",
        "timeslotStart": "2026-08-04T10:00:00",
        "timeslotEnd": "2026-08-04T12:00:00",
        "products": ["oat milk"],
        "limit": 10,
    }


def test_search_replacement_candidates_filters_plastic_bags():
    context = cart_context()
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch_response(
                "пакет",
                [
                    {"id": "milk", "name": "Молоко", "price": 45.0},
                    {"id": "bag1", "name": "Пакет біорозкладний 3 кг", "price": 2.0},
                    {"id": "bag2", "name": "Пакет Сільпо Пакет з Пакетів 18 кг", "price": 5.0},
                ],
            )
        }
    )

    candidates = search_replacement_candidates(client, context, "пакет")

    assert candidates == [{"id": "milk", "name": "Молоко", "price": 45.0}]


def test_search_replacement_candidates_no_results_returns_empty_list():
    context = cart_context()
    client = FakeClient({"silpo_find_products_batch": _batch_response("nonexistent", [])})

    candidates = search_replacement_candidates(client, context, "nonexistent")

    assert candidates == []
