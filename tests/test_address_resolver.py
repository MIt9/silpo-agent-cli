from silpo_agent.address_resolver import resolve_address
from silpo_agent.log_store import ReorderLogStore


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return self.responses.get(tool)


class FakeLogStore:
    def __init__(self):
        self.runs = []

    def append_run(self, run):
        self.runs.append(run)


def make_input(*answers):
    it = iter(answers)

    def input_fn(prompt=""):
        return next(it)

    return input_fn


def test_default_address_proposed_and_accepted():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Khreshchatyk 1"},
                {"id": "a2", "is_default": False, "address": "Kyiv, Sumska 2"},
            ]
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input("y"), print_fn=lambda *a: None)

    assert resolved.id == "a1"
    assert resolved.label == "Kyiv, Khreshchatyk 1"
    assert log_store.runs == [
        {"timestamp": log_store.runs[0]["timestamp"], "address": "Kyiv, Khreshchatyk 1", "address_id": "a1"}
    ]


def test_default_declined_then_pick_from_remaining_list():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Khreshchatyk 1"},
                {"id": "a2", "is_default": False, "address": "Kyiv, Sumska 2"},
                {"id": "a3", "is_default": False, "address": "Kyiv, Peremohy 3"},
            ]
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input("n", "2"), print_fn=lambda *a: None)

    assert resolved.id == "a3"
    assert resolved.label == "Kyiv, Peremohy 3"
    assert log_store.runs[0]["address_id"] == "a3"
    assert all(call[0] != "silpo_find_address" for call in client.calls)


def test_default_declined_then_type_new_address():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Khreshchatyk 1"},
            ],
            "silpo_find_address": [{"id": "new-1", "address": "Lviv, Rynok Sq 5"}],
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(
        client, log_store, input_fn=make_input("n", "Lviv, Rynok Sq 5"), print_fn=lambda *a: None
    )

    assert resolved.id == "new-1"
    assert resolved.label == "Lviv, Rynok Sq 5"
    assert ("silpo_find_address", {"query": "Lviv, Rynok Sq 5"}) in client.calls
    assert ("silpo_get_available_delivery_types", {"address_id": "new-1"}) in client.calls
    assert log_store.runs[0]["address_id"] == "new-1"


def test_default_always_proposed_first_regardless_of_prior_log_history(tmp_path):
    """CONTEXT.md's 'Delivery address resolution' entry: the MCP-marked
    default is proposed first every run, even if a prior run's Reorder Log
    recorded that the user picked a different address last time.
    """
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    log_store.append_run({"timestamp": "2026-08-01T10:00:00", "address": "Kyiv, Sumska 2", "address_id": "a2"})

    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": [
                {"id": "a1", "is_default": True, "address": "Kyiv, Khreshchatyk 1"},
                {"id": "a2", "is_default": False, "address": "Kyiv, Sumska 2"},
            ]
        }
    )
    prompts = []

    def input_fn(prompt=""):
        prompts.append(prompt)
        return "y"

    resolved = resolve_address(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert resolved.id == "a1"
    assert "Kyiv, Khreshchatyk 1" in prompts[0]


def test_zero_saved_addresses_falls_through_to_new_address():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": [],
            "silpo_find_address": [{"id": "new-1", "address": "Odesa, Deribasivska 1"}],
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(
        client, log_store, input_fn=make_input("Odesa, Deribasivska 1"), print_fn=lambda *a: None
    )

    assert resolved.id == "new-1"
    assert resolved.label == "Odesa, Deribasivska 1"
    assert ("silpo_find_address", {"query": "Odesa, Deribasivska 1"}) in client.calls
    assert log_store.runs[0]["address_id"] == "new-1"


def test_zero_saved_addresses_no_input_returns_none_and_does_not_log():
    client = FakeClient({"silpo_get_my_delivery_addresses": []})
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input(""), print_fn=lambda *a: None)

    assert resolved is None
    assert log_store.runs == []
    assert all(call[0] != "silpo_find_address" for call in client.calls)
