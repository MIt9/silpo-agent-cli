from silpo_agent.log_store import ReorderLogStore
from silpo_agent.order_aggregator import TypicalItem
from silpo_agent.substitution_resolver import resolve_substitutions


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


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


def test_available_item_passes_through_unchanged_no_replacement_call():
    client = FakeClient({"silpo_check_availability": {"available": True}})
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    log_store = FakeLogStore()

    result = resolve_substitutions(client, log_store, [item], print_fn=lambda *a: None)

    assert result.items == [item]
    assert result.substitutions == []
    assert result.unavailable == []
    assert all(call[0] != "silpo_get_replacements" for call in client.calls)


def test_zero_candidates_reported_unavailable_not_dropped_silently():
    client = FakeClient(
        {
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    log_store = FakeLogStore()

    result = resolve_substitutions(client, log_store, [item], print_fn=lambda *a: None)

    assert result.items == []
    assert result.substitutions == []
    assert result.unavailable == ["milk"]


def test_exactly_one_candidate_auto_applies_without_prompt():
    client = FakeClient(
        {
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [{"product_id": "milk-oat", "price": 50.0}],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    log_store = FakeLogStore()

    def input_fn(prompt=""):
        raise AssertionError("should not prompt for a single candidate")

    result = resolve_substitutions(client, log_store, [item], input_fn=input_fn, print_fn=lambda *a: None)

    assert result.items == [TypicalItem(product_id="milk-oat", frequency=1.0, last_known_price=50.0)]
    assert result.substitutions == [("milk", "milk-oat")]
    assert result.unavailable == []
    assert log_store.sets == []


def test_multiple_candidates_no_memory_asks_user_and_persists_choice():
    client = FakeClient(
        {
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [
                {"product_id": "milk-oat", "price": 50.0},
                {"product_id": "milk-soy", "price": 48.0},
            ],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    log_store = FakeLogStore()

    result = resolve_substitutions(
        client, log_store, [item], input_fn=make_input("2"), print_fn=lambda *a: None
    )

    assert result.items == [TypicalItem(product_id="milk-soy", frequency=1.0, last_known_price=48.0)]
    assert result.substitutions == [("milk", "milk-soy")]
    assert log_store.sets == [("milk", "milk-soy")]


def test_multiple_candidates_with_memory_reuses_choice_without_asking():
    client = FakeClient(
        {
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [
                {"product_id": "milk-oat", "price": 50.0},
                {"product_id": "milk-soy", "price": 48.0},
            ],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    log_store = FakeLogStore(substitutions={"milk": "milk-soy"})

    def input_fn(prompt=""):
        raise AssertionError("should not prompt when a memory entry exists")

    result = resolve_substitutions(client, log_store, [item], input_fn=input_fn, print_fn=lambda *a: None)

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
    candidates = [
        {"product_id": "milk-oat", "price": 50.0},
        {"product_id": "milk-soy", "price": 48.0},
    ]
    client = FakeClient({"silpo_check_availability": {"available": False}, "silpo_get_replacements": candidates})

    first = resolve_substitutions(client, log_store, [item], input_fn=make_input("2"), print_fn=lambda *a: None)
    assert first.items == [TypicalItem(product_id="milk-soy", frequency=1.0, last_known_price=48.0)]

    def input_fn(prompt=""):
        raise AssertionError("second run should not prompt again")

    second = resolve_substitutions(client, log_store, [item], input_fn=input_fn, print_fn=lambda *a: None)

    assert second.items == [TypicalItem(product_id="milk-soy", frequency=1.0, last_known_price=48.0)]


def test_invalid_pick_treated_as_unavailable_not_crash():
    client = FakeClient(
        {
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [
                {"product_id": "milk-oat", "price": 50.0},
                {"product_id": "milk-soy", "price": 48.0},
            ],
        }
    )
    item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    log_store = FakeLogStore()

    result = resolve_substitutions(
        client, log_store, [item], input_fn=make_input("99"), print_fn=lambda *a: None
    )

    assert result.items == []
    assert result.unavailable == ["milk"]
    assert log_store.sets == []


def test_multiple_typical_items_mixed_availability():
    client = FakeClient(
        {
            "silpo_check_availability": {"available": False},
            "silpo_get_replacements": [{"product_id": "milk-oat", "price": 50.0}],
        }
    )
    available_item = TypicalItem(product_id="bread", frequency=1.0, last_known_price=30.0)
    unavailable_item = TypicalItem(product_id="milk", frequency=1.0, last_known_price=45.0)
    log_store = FakeLogStore()

    # bread is available, milk is not -- vary the availability response per call
    def call(tool, args=None):
        client.calls.append((tool, args))
        if tool == "silpo_check_availability":
            return {"available": args["product_id"] == "bread"}
        return client.responses.get(tool)

    client.call = call

    result = resolve_substitutions(
        client, log_store, [available_item, unavailable_item], print_fn=lambda *a: None
    )

    assert result.items == [
        available_item,
        TypicalItem(product_id="milk-oat", frequency=1.0, last_known_price=50.0),
    ]
    assert result.substitutions == [("milk", "milk-oat")]
