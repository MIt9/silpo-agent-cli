from silpo_agent.product_search import ProductHit, search_products


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


class FakeContext:
    branch_id = "b1"
    company_id = "c1"
    delivery_type = "DeliveryHome"
    timeslot_start = "2026-08-05T10:00:00"
    timeslot_end = "2026-08-05T12:00:00"


def _batch(query, products):
    return {"success": True, "queries": [{"query": query, "totalFound": len(products), "products": products}]}


def test_search_sends_branch_context_and_returns_hits_with_slugs():
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                "milk",
                [{"id": "p1", "name": "Milk 1L", "price": 49.9, "oldPrice": 59.9, "stock": 12,
                  "available": True, "slug": "milk-1l-1"}],
            )
        }
    )

    hits = search_products(client, FakeContext(), "milk")

    assert len(hits) == 1
    assert hits[0].slug == "milk-1l-1"
    assert "milk-1l-1" in hits[0].format()
    tool, args = client.calls[0]
    assert tool == "silpo_find_products_batch"
    assert args["products"] == ["milk"]
    assert args["branchId"] == "b1"
    assert args["limit"] == 10


def test_search_filters_plastic_bags():
    client = FakeClient(
        {"silpo_find_products_batch": _batch("bag", [{"id": "p9", "name": "Пакет-майка", "price": 2.0}])}
    )

    assert search_products(client, FakeContext(), "bag") == []


def test_search_empty_result_returns_empty_without_error():
    client = FakeClient({"silpo_find_products_batch": _batch("xyz", [])})

    assert search_products(client, FakeContext(), "xyz") == []


def test_discounted_hit_format_shows_was_price():
    hit = ProductHit(name="Butter", price=49.9, old_price=69.9, slug="butter-1")

    assert "(was 69.90)" in hit.format()
    assert "butter-1" in hit.format()


def test_hit_without_slug_formats_without_none():
    hit = ProductHit(name="Butter", price=49.9)

    assert "None" not in hit.format()
