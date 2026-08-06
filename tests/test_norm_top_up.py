import pytest

from silpo_agent.cart_context import CartContext
from silpo_agent.norm_dataset import NormItem
from silpo_agent.norm_top_up import find_uncovered_categories, resolve_norm_top_up_items


class FakeClient:
    def __init__(self, responses: dict | None = None):
        self.calls = []
        self._responses = responses or {}

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self._responses.get(tool, {})


def _context():
    return CartContext(
        shopping_cart_id="cart-1",
        branch_id="b1",
        company_id="c1",
        delivery_type="DeliveryHome",
        timeslot_start="2026-08-05T10:00:00",
        timeslot_end="2026-08-05T12:00:00",
    )


def _categories_page(*slug_titles):
    categories = [{"id": f"id-{slug}", "slug": slug, "title": title} for slug, title in slug_titles]
    return {"success": True, "categories": categories, "meta": {"total": len(categories)}}


def _products_page(*ids):
    return {"success": True, "products": [{"id": pid} for pid in ids]}


def _batch(**query_to_product):
    """silpo_find_products_batch response: {query: product_or_None}."""
    return {
        "success": True,
        "queries": [
            {"query": query, "totalFound": 1 if product else 0, "products": [product] if product else []}
            for query, product in query_to_product.items()
        ],
    }


# --- find_uncovered_categories -------------------------------------------


def test_category_covered_by_existing_id_is_not_uncovered():
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient(
        {
            "silpo_get_categories": _categories_page(("ovochi-4808", "Овочі")),
            "silpo_get_products": _products_page("potato-real-id"),
        }
    )

    uncovered = find_uncovered_categories(client, _context(), norms, {"potato-real-id"})

    assert uncovered == []


def test_category_with_no_matching_id_is_uncovered():
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient(
        {
            "silpo_get_categories": _categories_page(("ovochi-4808", "Овочі")),
            "silpo_get_products": _products_page("some-other-potato-id"),
        }
    )

    uncovered = find_uncovered_categories(client, _context(), norms, {"typical-milk-id"})

    assert uncovered == norms


def test_presence_by_id_search_is_not_used_a_typical_items_own_id_never_matches_real_category_ids():
    """Regression for the ticket's explicit rejection: a typical item's own
    id is never a member of the category's real product-id set in this
    fixture, so it must correctly read as uncovered rather than covered."""
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient(
        {
            "silpo_get_categories": _categories_page(("ovochi-4808", "Овочі")),
            "silpo_get_products": _products_page("real-category-potato-id"),
        }
    )

    uncovered = find_uncovered_categories(client, _context(), norms, {"users-usual-potato-sku"})

    assert uncovered == norms


class _MultiCategoryClient:
    """silpo_get_products response depends on the requested category slug,
    which plain tool-name-keyed FakeClient can't express."""

    def __init__(self):
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        if tool == "silpo_get_categories":
            return _categories_page(("ovochi-4808", "Овочі"), ("frukty-4791", "Фрукти"))
        if tool == "silpo_get_products":
            if args["category"] == "ovochi-4808":
                return _products_page("potato-real-id")
            if args["category"] == "frukty-4791":
                return _products_page("some-other-apple-id")
        return {}


def test_multiple_categories_only_uncovered_ones_returned():
    norms = [
        NormItem("vegetables", "Картопля", 0.5, "kg"),
        NormItem("fruits", "Яблука", 0.5, "kg"),
    ]
    client = _MultiCategoryClient()

    uncovered = find_uncovered_categories(client, _context(), norms, {"potato-real-id"})

    assert uncovered == [norms[1]]


def test_unresolvable_category_is_treated_as_uncovered():
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient({"silpo_get_categories": _categories_page(("nothing-close", "Зовсім інше"))})

    uncovered = find_uncovered_categories(client, _context(), norms, {"whatever"})

    assert uncovered == norms
    assert all(call[0] != "silpo_get_products" for call in client.calls)


def test_category_guess_near_miss_falls_back_to_uncovered_and_warns():
    """Synthetic near-miss for the matching *logic*, independent of whatever
    `_CATEGORY_SEARCH_QUERY`'s current "dairy" guess is live-verified to be
    (2026-08-06: "Молочні продукти та яйця", see norm_top_up.py). This
    fixture's title, "Молочна продукція та яйця", is NOT a substring match
    for that guess either (different word forms: "Молочні"/"Молочна",
    "продукти"/"продукція"), so resolve_category_from_list still correctly
    returns None here. Proves the near-miss case degrades safely (uncovered,
    not a crash or a false "covered") and is no longer silent (AC: print_fn
    warns) -- if a future guess update ever makes this fixture title an
    accidental exact/substring match, this assertion is the tripwire that
    catches it."""
    norms = [NormItem("dairy", "Молоко", 0.5, "l")]
    client = FakeClient(
        {"silpo_get_categories": _categories_page(("molochna-produktsiya-1234", "Молочна продукція та яйця"))}
    )
    notes = []

    uncovered = find_uncovered_categories(client, _context(), norms, {"whatever"}, print_fn=notes.append)

    assert uncovered == norms
    assert all(call[0] != "silpo_get_products" for call in client.calls)
    assert any("dairy" in note for note in notes)


# --- resolve_norm_top_up_items --------------------------------------------


def test_uncovered_norm_item_gets_proposed_at_scaled_rounded_quantity():
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Картопля={
                    "id": "potato-1",
                    "name": "Картопля",
                    "price": 20.0,
                    "stock": 10,
                    "available": True,
                    "ratio": "кг",
                    "step": 0.5,
                    "companyId": "c1",
                    "branchId": "b1",
                }
            )
        }
    )

    result = resolve_norm_top_up_items(client, _context(), norms, people=1, print_fn=lambda *_: None)

    assert len(result.items) == 1
    item = result.items[0]
    assert item.product_id == "potato-1"
    assert item.quantity == 0.5  # 0.5 kg/person * 1 person, rounded to 0.5 step
    assert item.last_known_price == 20.0
    assert item.company_id == "c1"
    assert item.branch_id == "b1"
    assert item.name == "Картопля"
    assert result.skipped == []


def test_more_people_and_richer_basket_produce_larger_quantity():
    product = {
        "id": "potato-1",
        "name": "Картопля",
        "price": 20.0,
        "stock": 100,
        "available": True,
        "ratio": "кг",
        "step": 0.1,
        "companyId": "c1",
        "branchId": "b1",
    }
    client = FakeClient({"silpo_find_products_batch": _batch(Картопля=product)})

    basic_1_person = resolve_norm_top_up_items(
        client, _context(), [NormItem("vegetables", "Картопля", 0.5, "kg")], people=1, print_fn=lambda *_: None
    )
    premium_5_people = resolve_norm_top_up_items(
        client, _context(), [NormItem("vegetables", "Картопля", 0.5, "kg")], people=5, print_fn=lambda *_: None
    )

    assert premium_5_people.items[0].quantity > basic_1_person.items[0].quantity


def test_zero_search_results_are_skipped_not_added():
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient({"silpo_find_products_batch": _batch(Картопля=None)})
    notes = []

    result = resolve_norm_top_up_items(client, _context(), norms, people=1, print_fn=notes.append)

    assert result.items == []
    assert result.skipped == ["Картопля"]
    assert any("Картопля" in note for note in notes)


def test_unavailable_top_result_is_skipped_not_added():
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Картопля={"id": "potato-1", "price": 20.0, "stock": 0, "available": False}
            )
        }
    )

    result = resolve_norm_top_up_items(client, _context(), norms, people=1, print_fn=lambda *_: None)

    assert result.items == []
    assert result.skipped == ["Картопля"]


def test_ratio_compatible_liquid_product_rounds_up_to_whole_unit():
    """Product's `ratio` ("л") matches the norm's unit ("l") -- the kg/l
    target is directly usable, rounded up to a valid step (1 = whole
    units, since no `step` given here)."""
    norms = [NormItem("dairy", "Молоко", 0.6, "l")]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Молоко={
                    "id": "milk-1",
                    "name": "Молоко",
                    "price": 45.0,
                    "stock": 10,
                    "available": True,
                    "ratio": "л",
                    "companyId": "c1",
                    "branchId": "b1",
                }
            )
        }
    )

    result = resolve_norm_top_up_items(client, _context(), norms, people=3, print_fn=lambda *_: None)

    # 0.6 l/person * 3 people = 1.8, rounds up to 2 whole units
    assert result.items[0].quantity == 2


def test_ratio_incompatible_pack_product_defaults_to_one_with_a_note():
    """Live-observed real bug: a norm target in kg can't be converted into
    a "шт" (piece/pack) product's quantity without knowing the pack's real
    net weight, which isn't exposed anywhere on the product record. Must
    NOT silently ceil the kg figure as if it were a pack count (e.g. "need
    1.5kg" -> "add 2 packs" when each pack might only be 400g) -- falls
    back to 1 with a clear, printed note instead of a fabricated number."""
    norms = [NormItem("grains", "Гречка", 0.12, "kg")]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Гречка={
                    "id": "buckwheat-1",
                    "name": "Гречка",
                    "price": 60.0,
                    "stock": 10,
                    "available": True,
                    "ratio": "шт",
                    "step": 1,
                    "companyId": "c1",
                    "branchId": "b1",
                }
            )
        }
    )
    notes = []

    result = resolve_norm_top_up_items(client, _context(), norms, people=5, print_fn=notes.append)

    assert len(result.items) == 1
    assert result.items[0].quantity == 1
    assert any("Гречка" in note and "шт" in note for note in notes)


def test_ratio_missing_from_product_record_defaults_to_one_with_a_note():
    """No `ratio` at all -- unknown unit, same conservative fallback as an
    explicitly incompatible one, not an assumed match."""
    norms = [NormItem("dairy", "Молоко", 0.6, "l")]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Молоко={
                    "id": "milk-1",
                    "name": "Молоко",
                    "price": 45.0,
                    "stock": 10,
                    "available": True,
                    "companyId": "c1",
                    "branchId": "b1",
                }
            )
        }
    )
    notes = []

    result = resolve_norm_top_up_items(client, _context(), norms, people=3, print_fn=notes.append)

    assert result.items[0].quantity == 1
    assert any("Молоко" in note for note in notes)


# --- ticket 06: seasonal composition --------------------------------------


def test_seasonal_out_of_season_item_flows_through_scaled_quantity_and_note_flag():
    """The already-scaled `quantity_per_person` (as if norm_dataset.get_norms
    had already applied the out-of-season multiplier) is what feeds the
    existing ratio-compatibility/round_to_step math -- no separate/parallel
    quantity path -- while `out_of_season_items` separately flags the
    item for cli.py's printed note, sourced from the NormItem's own profile."""
    # Represents get_norms("eco", month=3)'s already-scaled apple: base 0.5
    # kg/person * 1.2 out-of-season multiplier = 0.6.
    norms = [NormItem("fruits", "Яблука", 0.6, "kg", in_season_months=(8, 9, 10, 11, 12), out_of_season_multiplier=1.2)]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Яблука={
                    "id": "apple-1",
                    "name": "Яблука",
                    "price": 30.0,
                    "stock": 50,
                    "available": True,
                    "ratio": "кг",
                    "step": 0.1,
                    "companyId": "c1",
                    "branchId": "b1",
                }
            )
        }
    )

    result = resolve_norm_top_up_items(client, _context(), norms, people=1, month=3, print_fn=lambda *_: None)

    assert result.items[0].quantity == pytest.approx(0.6)  # raw_quantity 0.6 * step 0.1 -> already a valid step
    assert result.items[0] in result.out_of_season_items


def test_seasonal_item_in_season_month_is_not_flagged():
    norms = [NormItem("fruits", "Яблука", 0.5, "kg", in_season_months=(8, 9, 10, 11, 12), out_of_season_multiplier=1.2)]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Яблука={
                    "id": "apple-1",
                    "name": "Яблука",
                    "price": 30.0,
                    "stock": 50,
                    "available": True,
                    "ratio": "кг",
                    "step": 0.1,
                    "companyId": "c1",
                    "branchId": "b1",
                }
            )
        }
    )

    result = resolve_norm_top_up_items(client, _context(), norms, people=1, month=9, print_fn=lambda *_: None)

    assert result.out_of_season_items == frozenset()


def test_no_profile_item_never_flagged_regardless_of_month():
    norms = [NormItem("vegetables", "Картопля", 0.5, "kg")]
    client = FakeClient(
        {
            "silpo_find_products_batch": _batch(
                Картопля={
                    "id": "potato-1",
                    "name": "Картопля",
                    "price": 20.0,
                    "stock": 10,
                    "available": True,
                    "ratio": "кг",
                    "step": 0.5,
                    "companyId": "c1",
                    "branchId": "b1",
                }
            )
        }
    )

    result = resolve_norm_top_up_items(client, _context(), norms, people=1, month=3, print_fn=lambda *_: None)

    assert result.out_of_season_items == frozenset()
