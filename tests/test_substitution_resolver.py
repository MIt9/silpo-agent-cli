from silpo_agent.cart_context import CartContext
from silpo_agent.log_store import ReorderLogStore
from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.substitution_resolver import resolve_substitutions

CART_CONTEXT = CartContext(
    shopping_cart_id="cart-1",
    branch_id="branch-1",
    company_id="company-1",
    delivery_type="COURIER",
    timeslot_start="2026-08-04T10:00:00Z",
    timeslot_end="2026-08-04T11:00:00Z",
    validations=[],
)


class FakeClient:
    """Dispatches by tool name to a canned response dict, or to a custom
    per-tool callable when a test needs the response to vary by args."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        response = self.responses.get(tool)
        if callable(response):
            return response(args)
        return response


class FakeLogStore:
    def __init__(self, substitutions=None):
        self.substitutions = dict(substitutions or {})
        self.sets = []

    def get_substitution(self, item_id):
        return self.substitutions.get(item_id)

    def set_substitution(self, item_id, replacement_id):
        self.substitutions[item_id] = replacement_id
        self.sets.append((item_id, replacement_id))


def make_input(*answers):
    it = iter(answers)

    def input_fn(prompt=""):
        return next(it)

    return input_fn


def _available_batch_response(product_ids, unavailable_ids=()):
    """Build a silpo_find_products_batch response: real shape is
    {"queries": [{"query", "totalFound", "products": [...]}]}, product
    records use the general shape ("id", "stock", "available", ...)."""
    queries = []
    for product_id in product_ids:
        is_available = product_id not in unavailable_ids
        products = (
            [{"id": product_id, "name": product_id, "price": 45.0, "stock": 5, "available": True}]
            if is_available
            else [{"id": product_id, "name": product_id, "price": 45.0, "stock": 0, "available": False}]
        )
        queries.append({"query": product_id, "totalFound": len(products), "products": products})
    return {"success": True, "queries": queries}


def test_available_item_passes_through_unchanged_no_replacement_call():
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient({"silpo_find_products_batch": _available_batch_response(["milk"])})
    log_store = FakeLogStore()

    result = resolve_substitutions(client, log_store, [item], CART_CONTEXT, print_fn=lambda *a: None)

    assert result.items == [item]
    assert result.substitutions == []
    assert result.unavailable == []
    assert all(call[0] != "silpo_get_replacements" for call in client.calls)
    assert all(call[0] != "silpo_check_availability" for call in client.calls)

    # availability check must use real batch shape sourced from CartContext
    find_call = next(c for c in client.calls if c[0] == "silpo_find_products_batch")
    assert find_call[1]["branchId"] == "branch-1"
    assert find_call[1]["deliveryType"] == "COURIER"
    assert find_call[1]["timeslotStart"] == "2026-08-04T10:00:00Z"
    assert find_call[1]["timeslotEnd"] == "2026-08-04T11:00:00Z"
    assert find_call[1]["products"] == ["milk"]


def test_zero_candidates_reported_unavailable_not_dropped_silently():
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient(
        {
            "silpo_find_products_batch": _available_batch_response(["milk"], unavailable_ids=["milk"]),
            "silpo_get_replacements": {"success": True, "summary": "Found replacements for 0 products", "items": []},
        }
    )
    log_store = FakeLogStore()

    result = resolve_substitutions(client, log_store, [item], CART_CONTEXT, print_fn=lambda *a: None)

    assert result.items == []
    assert result.substitutions == []
    assert result.unavailable == ["milk"]

    replacements_call = next(c for c in client.calls if c[0] == "silpo_get_replacements")
    assert replacements_call[1] == {
        "branchId": "branch-1",
        "companyId": "company-1",
        "productIds": ["milk"],
        "deliveryType": "COURIER",
    }


def test_exactly_one_candidate_auto_applies_without_prompt():
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient(
        {
            "silpo_find_products_batch": _available_batch_response(["milk"], unavailable_ids=["milk"]),
            "silpo_get_replacements": {
                "success": True,
                "summary": "Found replacements for 1 products",
                "items": [{"productId": "milk", "replacements": [{"id": "milk-oat", "price": 50.0}]}],
            },
        }
    )
    log_store = FakeLogStore()

    def input_fn(prompt=""):
        raise AssertionError("should not prompt for a single candidate")

    result = resolve_substitutions(
        client, log_store, [item], CART_CONTEXT, input_fn=input_fn, print_fn=lambda *a: None
    )

    assert result.items == [TypicalItem(product_id="milk-oat", frequency=1.0, last_known_price=50.0)]
    assert result.substitutions == [("milk", "milk-oat")]
    assert result.unavailable == []
    assert log_store.sets == []


def test_multiple_candidates_no_memory_asks_user_and_persists_choice():
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient(
        {
            "silpo_find_products_batch": _available_batch_response(["milk"], unavailable_ids=["milk"]),
            "silpo_get_replacements": {
                "success": True,
                "summary": "Found replacements for 1 products",
                "items": [
                    {
                        "productId": "milk",
                        "replacements": [
                            {"id": "milk-oat", "price": 50.0},
                            {"id": "milk-soy", "price": 48.0},
                        ],
                    }
                ],
            },
        }
    )
    log_store = FakeLogStore()

    result = resolve_substitutions(
        client, log_store, [item], CART_CONTEXT, input_fn=make_input("2"), print_fn=lambda *a: None
    )

    assert result.items == [TypicalItem(product_id="milk-soy", frequency=1.0, last_known_price=48.0)]
    assert result.substitutions == [("milk", "milk-soy")]
    assert log_store.sets == [("milk", "milk-soy")]


def test_multiple_candidates_with_memory_reuses_choice_without_asking():
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient(
        {
            "silpo_find_products_batch": _available_batch_response(["milk"], unavailable_ids=["milk"]),
            "silpo_get_replacements": {
                "success": True,
                "summary": "Found replacements for 1 products",
                "items": [
                    {
                        "productId": "milk",
                        "replacements": [
                            {"id": "milk-oat", "price": 50.0},
                            {"id": "milk-soy", "price": 48.0},
                        ],
                    }
                ],
            },
        }
    )
    log_store = FakeLogStore(substitutions={"milk": "milk-soy"})

    def input_fn(prompt=""):
        raise AssertionError("should not prompt when a memory entry exists")

    result = resolve_substitutions(
        client, log_store, [item], CART_CONTEXT, input_fn=input_fn, print_fn=lambda *a: None
    )

    assert result.items == [TypicalItem(product_id="milk-soy", frequency=1.0, last_known_price=48.0)]
    assert result.substitutions == [("milk", "milk-soy")]
    assert log_store.sets == []


def test_real_log_store_second_run_reuses_saved_choice(tmp_path):
    """End-to-end memory persistence through the real ReorderLogStore: a
    second run with the same multi-candidate situation reuses the choice
    saved on the first run instead of asking again (acceptance criterion).
    """
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    replacements_response = {
        "success": True,
        "summary": "Found replacements for 1 products",
        "items": [
            {
                "productId": "milk",
                "replacements": [
                    {"id": "milk-oat", "price": 50.0},
                    {"id": "milk-soy", "price": 48.0},
                ],
            }
        ],
    }
    client = FakeClient(
        {
            "silpo_find_products_batch": _available_batch_response(["milk"], unavailable_ids=["milk"]),
            "silpo_get_replacements": replacements_response,
        }
    )

    first = resolve_substitutions(
        client, log_store, [item], CART_CONTEXT, input_fn=make_input("2"), print_fn=lambda *a: None
    )
    assert first.items == [TypicalItem(product_id="milk-soy", frequency=1.0, last_known_price=48.0)]

    def input_fn(prompt=""):
        raise AssertionError("second run should not prompt again")

    second = resolve_substitutions(
        client, log_store, [item], CART_CONTEXT, input_fn=input_fn, print_fn=lambda *a: None
    )

    assert second.items == [TypicalItem(product_id="milk-soy", frequency=1.0, last_known_price=48.0)]


def test_invalid_pick_treated_as_unavailable_not_crash():
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient(
        {
            "silpo_find_products_batch": _available_batch_response(["milk"], unavailable_ids=["milk"]),
            "silpo_get_replacements": {
                "success": True,
                "summary": "Found replacements for 1 products",
                "items": [
                    {
                        "productId": "milk",
                        "replacements": [
                            {"id": "milk-oat", "price": 50.0},
                            {"id": "milk-soy", "price": 48.0},
                        ],
                    }
                ],
            },
        }
    )
    log_store = FakeLogStore()

    result = resolve_substitutions(
        client, log_store, [item], CART_CONTEXT, input_fn=make_input("99"), print_fn=lambda *a: None
    )

    assert result.items == []
    assert result.unavailable == ["milk"]
    assert log_store.sets == []


def test_multiple_typical_items_mixed_availability_batches_calls():
    available_item = TypicalItem(product_id="bread", frequency=1.0, last_known_price=30.0)
    unavailable_item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient(
        {
            "silpo_find_products_batch": _available_batch_response(["bread", "milk"], unavailable_ids=["milk"]),
            "silpo_get_replacements": {
                "success": True,
                "summary": "Found replacements for 1 products",
                "items": [{"productId": "milk", "replacements": [{"id": "milk-oat", "price": 50.0}]}],
            },
        }
    )
    log_store = FakeLogStore()

    result = resolve_substitutions(
        client, log_store, [available_item, unavailable_item], CART_CONTEXT, print_fn=lambda *a: None
    )

    assert result.items == [
        available_item,
        TypicalItem(product_id="milk-oat", frequency=1.0, last_known_price=50.0),
    ]
    assert result.substitutions == [("milk", "milk-oat")]

    # exactly one batched availability call and one batched replacements call,
    # not one call per item
    find_calls = [c for c in client.calls if c[0] == "silpo_find_products_batch"]
    replacement_calls = [c for c in client.calls if c[0] == "silpo_get_replacements"]
    assert len(find_calls) == 1
    assert find_calls[0][1]["products"] == ["bread", "milk"]
    assert len(replacement_calls) == 1
    assert replacement_calls[0][1]["productIds"] == ["milk"]


def test_no_unavailable_items_skips_replacements_call_entirely():
    item = TypicalItem(product_id="bread", frequency=1.0, last_known_price=30.0)
    client = FakeClient({"silpo_find_products_batch": _available_batch_response(["bread"])})
    log_store = FakeLogStore()

    result = resolve_substitutions(client, log_store, [item], CART_CONTEXT, print_fn=lambda *a: None)

    assert result.items == [item]
    assert all(call[0] != "silpo_get_replacements" for call in client.calls)


def test_no_cart_context_yet_skips_lookups_and_reports_unavailable_without_crashing():
    """resolve_cart_context returns an all-None CartContext on a first-ever
    run or a cleared cart (see cart_context.py). Sending None branchId/
    companyId/etc. to the real MCP tools would fail -- both lookups must be
    skipped entirely, with items reported unavailable rather than a crash."""
    no_cart_context = CartContext(
        shopping_cart_id=None,
        branch_id=None,
        company_id=None,
        delivery_type=None,
        timeslot_start=None,
        timeslot_end=None,
        validations=[],
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    client = FakeClient({})
    log_store = FakeLogStore()

    result = resolve_substitutions(client, log_store, [item], no_cart_context, print_fn=lambda *a: None)

    assert result.items == []
    assert result.substitutions == []
    assert result.unavailable == ["milk"]
    assert client.calls == []


def test_availability_check_uses_item_name_as_query_when_present():
    """TypicalItem.name (added in issue #19) is a far better free-text
    search query than a raw product_id/UUID -- used when present, matched
    back to the item via the response's queries[].query, and falls back to
    product_id only for items without a name (e.g. substitution results)."""
    named_item = TypicalItem(product_id="sku-123", frequency=1.0, last_known_price=45.0, name="Молоко 2.5%")
    unnamed_item = TypicalItem(product_id="bread", frequency=1.0, last_known_price=30.0)
    client = FakeClient(
        {
            "silpo_find_products_batch": {
                "success": True,
                "queries": [
                    {
                        "query": "Молоко 2.5%",
                        "totalFound": 1,
                        "products": [
                            {"id": "sku-123", "name": "Молоко 2.5%", "price": 45.0, "stock": 5, "available": True}
                        ],
                    },
                    {
                        "query": "bread",
                        "totalFound": 1,
                        "products": [{"id": "bread", "name": "bread", "price": 30.0, "stock": 5, "available": True}],
                    },
                ],
            }
        }
    )
    log_store = FakeLogStore()

    result = resolve_substitutions(
        client, log_store, [named_item, unnamed_item], CART_CONTEXT, print_fn=lambda *a: None
    )

    assert result.items == [named_item, unnamed_item]
    find_call = next(c for c in client.calls if c[0] == "silpo_find_products_batch")
    assert find_call[1]["products"] == ["Молоко 2.5%", "bread"]
