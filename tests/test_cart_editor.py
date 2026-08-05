from silpo_agent.cart_context import CartContext
from silpo_agent.cart_editor import CartEditError, search_replacement_candidates, swap_cart_item


class FakeClient:
    def __init__(self, responses: dict | None = None):
        self.calls = []
        self._responses = responses or {}

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self._responses.get(tool)


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
