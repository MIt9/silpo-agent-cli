from silpo_agent.auth import MCPError
from silpo_agent.cart_context import CartContext
from silpo_agent.cart_editor import CartEditError, search_replacement_candidates, swap_cart_item


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


# --- swap_cart_item ---------------------------------------------------


def test_swap_cart_item_removes_old_and_adds_new():
    context = cart_context(products=[{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}])
    client = FakeClient()
    new_product = {"id": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0, "name": "Oat Milk"}

    result = swap_cart_item(client, context, "milk", new_product)

    assert result.removed_product_id == "milk"
    assert result.added_product_id == "oat-milk"
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
                    "comment": None,
                }
            ],
        },
    ) in client.calls
    # remove must happen before add
    tool_order = [tool for tool, _ in client.calls]
    assert tool_order.index("silpo_remove_cart_products") < tool_order.index("silpo_add_or_update_cart_products")


def test_swap_cart_item_preserves_old_quantity():
    context = cart_context(products=[{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 3}])
    client = FakeClient()
    new_product = {"id": "oat-milk", "companyId": "c2", "branchId": "b2", "price": 55.0}

    swap_cart_item(client, context, "milk", new_product)

    added_call = next(c for c in client.calls if c[0] == "silpo_add_or_update_cart_products")
    assert added_call[1]["products"][0]["quantity"] == 3


def test_swap_cart_item_falls_back_to_cart_context_branch_and_company():
    context = cart_context(
        products=[{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}],
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
    context = cart_context(products=[{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 2}])

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
    context = cart_context(products=[{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}])

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
    context = cart_context(products=[{"productId": "milk", "companyId": "c1", "branchId": "b1", "quantity": 1}])
    client = FakeClient()

    try:
        swap_cart_item(client, context, "milk", {"price": 10.0})
        assert False, "expected CartEditError"
    except CartEditError:
        pass

    assert client.calls == []


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
