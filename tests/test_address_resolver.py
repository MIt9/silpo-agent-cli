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


def addresses_response(*addresses):
    """Real `silpo_get_my_delivery_addresses` shape: wrapped, not a bare list."""
    return {"success": True, "summary": f"Found {len(addresses)} delivery addresses", "addresses": list(addresses)}


def find_address_response(*addresses):
    """Real `silpo_find_address` shape: wrapped, not a bare list."""
    return {"success": True, "summary": f"Found {len(addresses)} addresses", "addresses": list(addresses)}


def saved_address(id, city, street, building, apartment=None, latitude=49.1, longitude=28.1, tag=None):
    return {
        "id": id,
        "tag": tag,
        "city": city,
        "street": street,
        "building": building,
        "apartment": apartment,
        "floor": None,
        "entrance": None,
        "latitude": latitude,
        "longitude": longitude,
        "comment": None,
    }


def geocoded_address(address, city, street, house_number, district="...", latitude=49.2, longitude=28.2):
    return {
        "address": address,
        "city": city,
        "street": street,
        "houseNumber": house_number,
        "district": district,
        "latitude": latitude,
        "longitude": longitude,
    }


def test_first_address_proposed_and_accepted():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Вінниця", "Варшавська вулиця", "27", apartment="25"),
                saved_address("a2", "Київ", "Сумська вулиця", "2"),
            )
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input("y"), print_fn=lambda *a: None)

    assert resolved.id == "a1"
    assert resolved.label == "Вінниця, Варшавська вулиця, 27, кв. 25"
    assert log_store.runs == [
        {
            "timestamp": log_store.runs[0]["timestamp"],
            "address": "Вінниця, Варшавська вулиця, 27, кв. 25",
            "address_id": "a1",
        }
    ]


def test_first_address_confirmation_prompt_never_shows_raw_uuid():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("9930af7e-07be-4f3a-898a-3ed7435ec655", "Львів", "Ринок", "5"),
            )
        }
    )
    log_store = FakeLogStore()
    prompts = []

    def input_fn(prompt=""):
        prompts.append(prompt)
        return "y"

    resolve_address(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert "9930af7e-07be-4f3a-898a-3ed7435ec655" not in prompts[0]
    assert "Львів, Ринок, 5" in prompts[0]


def test_accepting_saved_address_looks_up_delivery_types_by_coordinates():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Вінниця", "Варшавська вулиця", "27", latitude=49.233, longitude=28.468),
            )
        }
    )
    log_store = FakeLogStore()

    resolve_address(client, log_store, input_fn=make_input("y"), print_fn=lambda *a: None)

    assert ("silpo_get_available_delivery_types", {"latitude": 49.233, "longitude": 28.468}) in client.calls


def test_first_declined_then_pick_from_remaining_list():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Київ", "Хрещатик", "1"),
                saved_address("a2", "Київ", "Сумська вулиця", "2"),
                saved_address("a3", "Київ", "Перемоги", "3"),
            )
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input("n", "2"), print_fn=lambda *a: None)

    assert resolved.id == "a3"
    assert resolved.label == "Київ, Перемоги, 3"
    assert log_store.runs[0]["address_id"] == "a3"
    assert all(call[0] != "silpo_find_address" for call in client.calls)


def test_out_of_range_number_choice_returns_none_and_does_not_search():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Київ", "Хрещатик", "1"),
                saved_address("a2", "Київ", "Сумська вулиця", "2"),
            )
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input("n", "99"), print_fn=lambda *a: None)

    assert resolved is None
    assert log_store.runs == []
    assert all(call[0] != "silpo_find_address" for call in client.calls)
    assert all(call[0] != "silpo_get_available_delivery_types" for call in client.calls)


def test_first_declined_then_type_new_address():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Київ", "Хрещатик", "1"),
            ),
            "silpo_find_address": find_address_response(
                geocoded_address("Вінниця, Варшавська вулиця, 27", "Вінниця", "Варшавська вулиця", "27",
                                  latitude=49.233, longitude=28.468),
            ),
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(
        client, log_store, input_fn=make_input("n", "Вінниця, Варшавська вулиця, 27"), print_fn=lambda *a: None
    )

    assert resolved.id is None
    assert resolved.label == "Вінниця, Варшавська вулиця, 27"
    assert ("silpo_find_address", {"address": "Вінниця, Варшавська вулиця, 27"}) in client.calls
    assert ("silpo_get_available_delivery_types", {"latitude": 49.233, "longitude": 28.468}) in client.calls
    assert log_store.runs[0]["address_id"] is None


def test_default_always_proposed_first_regardless_of_prior_log_history(tmp_path):
    """CONTEXT.md's 'Delivery address resolution' entry: the first address as
    returned by the API is proposed first every run, even if a prior run's
    Reorder Log recorded that the user picked a different address last time.
    MCP does not mark any address as a server-side default.
    """
    log_store = ReorderLogStore(tmp_path / "reorder_log.json")
    log_store.append_run({"timestamp": "2026-08-01T10:00:00", "address": "Київ, Сумська вулиця, 2", "address_id": "a2"})

    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Київ", "Хрещатик", "1"),
                saved_address("a2", "Київ", "Сумська вулиця", "2"),
            )
        }
    )
    prompts = []

    def input_fn(prompt=""):
        prompts.append(prompt)
        return "y"

    resolved = resolve_address(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert resolved.id == "a1"
    assert "Хрещатик" in prompts[0]


def test_zero_saved_addresses_falls_through_to_new_address():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(),
            "silpo_find_address": find_address_response(
                geocoded_address("Одеса, Дерибасівська, 1", "Одеса", "Дерибасівська", "1"),
            ),
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(
        client, log_store, input_fn=make_input("Одеса, Дерибасівська, 1"), print_fn=lambda *a: None
    )

    assert resolved.id is None
    assert resolved.label == "Одеса, Дерибасівська, 1"
    assert ("silpo_find_address", {"address": "Одеса, Дерибасівська, 1"}) in client.calls
    assert log_store.runs[0]["address_id"] is None


def test_zero_saved_addresses_no_input_returns_none_and_does_not_log():
    client = FakeClient({"silpo_get_my_delivery_addresses": addresses_response()})
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input(""), print_fn=lambda *a: None)

    assert resolved is None
    assert log_store.runs == []
    assert all(call[0] != "silpo_find_address" for call in client.calls)


def test_saved_address_label_omits_apartment_when_absent():
    client = FakeClient(
        {
            "silpo_get_my_delivery_addresses": addresses_response(
                saved_address("a1", "Київ", "Хрещатик", "1", apartment=None),
            )
        }
    )
    log_store = FakeLogStore()

    resolved = resolve_address(client, log_store, input_fn=make_input("y"), print_fn=lambda *a: None)

    assert resolved.label == "Київ, Хрещатик, 1"
