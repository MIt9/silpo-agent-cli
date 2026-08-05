from silpo_agent.delivery_settings import run_delivery_settings


class FakeClient:
    """Same FakeClient seam every other module's tests use (see
    test_address_resolver.py/test_promo_optimizer.py/test_cli.py). Extended
    with list-shaped responses so a tool called more than once (here,
    silpo_get_shopping_cart_by_id: once for the pre-apply template, once for
    the post-apply availability re-check) can return a different fixture per
    call, popped in order.
    """

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        response = self.responses.get(tool)
        if isinstance(response, list):
            return response.pop(0) if response else None
        return response


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
    return {"success": True, "summary": f"Found {len(addresses)} delivery addresses", "addresses": list(addresses)}


def saved_address(id, city, street, building, latitude=49.24, longitude=28.48):
    return {
        "id": id,
        "tag": None,
        "city": city,
        "street": street,
        "building": building,
        "apartment": None,
        "floor": None,
        "entrance": None,
        "latitude": latitude,
        "longitude": longitude,
        "comment": None,
    }


def delivery_types_response(*options):
    """Real silpo_get_available_delivery_types shape, live-verified
    2026-08-05: {"success", "summary", "options": [{"deliveryType",
    "branchId", "description"}]} -- NOT "deliveryTypes"."""
    return {"success": True, "summary": f"Found {len(options)} delivery options", "options": list(options)}


def time_slots_response(*slots):
    return {"success": True, "summary": f"Found {len(slots)} slots", "slots": list(slots), "meta": {"total": len(slots)}}


def branches_response(*branches):
    """Real silpo_list_branches shape, live-verified 2026-08-05: {"success",
    "summary", "branches": [{"branchId", "companyId", "externalId", "city",
    "address", "latitude", "longitude", "hasPickup", "open"}], "meta"} --
    field names are "city"/"address", NOT "cityFull"/"addressFull" as the
    silpo_update_shopping_cart tool description's own text claims."""
    return {
        "success": True,
        "summary": f"Found {len(branches)} branches (total: {len(branches)})",
        "branches": list(branches),
        "meta": {"limit": 50, "offset": 0, "total": len(branches)},
    }


def branch(branch_id, city, address, latitude, longitude, company_id="c-branch"):
    return {
        "branchId": branch_id,
        "companyId": company_id,
        "externalId": "1",
        "city": city,
        "address": address,
        "latitude": latitude,
        "longitude": longitude,
        "hasPickup": True,
        "open": True,
    }


def settlements_response(*settlements):
    """Real silpo_find_nova_poshta_settlements shape, live-verified
    2026-08-05: {"success", "summary", "settlements": [{"id", "title",
    "area", "region"}]}."""
    return {"success": True, "summary": f"Found {len(settlements)} settlements", "settlements": list(settlements)}


def offices_response(*offices):
    """Real silpo_find_nova_poshta_offices shape, live-verified 2026-08-05:
    {"success", "summary", "offices": [{"id", "title", "address", "type",
    "number", "status", "latitude", "longitude"}], "meta"} -- latitude/
    longitude are numbers here (unlike branches, where they're strings)."""
    return {"success": True, "summary": f"Found {len(offices)} offices", "offices": list(offices), "meta": {"total": len(offices)}}


def cart_by_id_response(address=None, shipments=None, products=None, validations=None, delivery_type="DeliveryHome"):
    shipments = shipments if shipments is not None else [
        {"id": "ship-1", "companyId": "c1", "branchId": "old-branch", "products": products or []}
    ]
    return {
        "success": True,
        "cart": {
            "id": "cart-1",
            "deliveryType": delivery_type,
            "timeslot": {"start": "2026-08-04T10:00:00", "end": "2026-08-04T12:00:00"},
            "address": address if address is not None else {
                "addressType": "flat",
                "latitude": "49.24",
                "longitude": "28.48",
                "city": "Вінниця",
                "street": "Варшавська вулиця",
                "house": "27",
            },
            "shipments": shipments,
            "calculation": {"validations": validations or []},
        },
        "loyalty": {},
    }


def _base_responses(**overrides):
    """Everything needed for a full happy-path run: address resolution,
    cart context template, delivery-type listing, timeslot listing, and the
    update call itself."""
    responses = {
        "silpo_get_my_delivery_addresses": addresses_response(
            saved_address("a1", "Вінниця", "Варшавська вулиця", "27", latitude=49.24, longitude=28.48)
        ),
        "silpo_get_my_shopping_cart": {"success": True, "shoppingCartId": "cart-1"},
        "silpo_get_shopping_cart_by_id": [cart_by_id_response()],
        "silpo_get_available_delivery_types": delivery_types_response(
            {"deliveryType": "DeliveryHome", "branchId": "new-branch", "description": "Regular delivery"},
            {"deliveryType": "SelfPickup", "branchId": None, "description": "Self pickup"},
            {"deliveryType": "NovaPoshta", "branchId": None, "description": "Nova Poshta"},
        ),
        "silpo_get_time_slots": time_slots_response(
            {"start": "2026-08-06T10:00:00", "end": "2026-08-06T12:00:00", "available": True},
            {"start": "2026-08-06T12:00:00", "end": "2026-08-06T14:00:00", "available": False},
        ),
        "silpo_update_shopping_cart": {"success": True},
    }
    responses.update(overrides)
    return responses


def test_happy_path_end_to_end_applies_delivery_settings():
    client = FakeClient(_base_responses())
    log_store = FakeLogStore()
    # address: accept first saved -> delivery type: pick #1 (DeliveryHome) -> timeslot: pick #1 (the only available one)
    input_fn = make_input("y", "1", "1")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is True
    assert result.delivery_type == "DeliveryHome"
    assert (
        "silpo_update_shopping_cart",
        {
            "shoppingCartId": "cart-1",
            "deliveryType": "DeliveryHome",
            "timeslot": {"start": "2026-08-06T10:00:00", "end": "2026-08-06T12:00:00"},
            "address": {
                "addressType": "flat",
                "latitude": "49.24",
                "longitude": "28.48",
                "city": "Вінниця",
                "street": "Варшавська вулиця",
                "house": "27",
            },
            "shipments": [{"id": "ship-1", "companyId": "c1", "branchId": "new-branch", "products": []}],
        },
    ) in client.calls
    # Only the available slot must be choosable -- the unavailable one filtered out before prompting.
    assert client.calls.count(("silpo_get_time_slots", {"branchId": "new-branch", "deliveryTypes": ["DeliveryHome"]})) == 1


def test_post_apply_report_identifies_newly_unavailable_cart_items():
    pre_products = [
        {"productId": "milk", "companyId": "c1", "branchId": "old-branch", "name": "Молоко"},
        {"productId": "eggs", "companyId": "c1", "branchId": "old-branch", "name": "Яйця"},
    ]
    post_validations = [
        {"level": "error", "type": "product", "message": "product.offer.stock.max", "context": {"productId": "milk", "stock": 0}},
        {"level": "error", "type": "timeslot", "message": "timeslot.not_found", "context": []},
    ]
    responses = _base_responses(
        silpo_get_shopping_cart_by_id=[
            cart_by_id_response(products=pre_products),  # pre-apply template + pre-update products snapshot
            cart_by_id_response(validations=post_validations),  # post-apply re-resolve
        ]
    )
    client = FakeClient(responses)
    log_store = FakeLogStore()
    input_fn = make_input("y", "1", "1")
    printed = []

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: printed.append(" ".join(str(x) for x in a)))

    assert result.applied is True
    assert result.newly_unavailable == [pre_products[0]]
    joined = "\n".join(printed)
    assert "Молоко" in joined
    assert "Яйця" not in joined


def test_invalid_timeslot_choice_does_not_apply():
    client = FakeClient(_base_responses())
    log_store = FakeLogStore()
    input_fn = make_input("y", "1", "99")  # 99 is out of range for the single available slot

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False


def test_timeslot_missing_start_or_end_does_not_apply():
    """A malformed silpo_get_time_slots entry (available=true but no real
    start/end) must not reach silpo_update_shopping_cart -- same
    abort-cleanly guard as every other invalid-selection case."""
    responses = _base_responses(
        silpo_get_time_slots=time_slots_response(
            {"start": None, "end": None, "available": True},
        )
    )
    client = FakeClient(responses)
    log_store = FakeLogStore()
    input_fn = make_input("y", "1", "1")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)


def test_unsupported_delivery_type_selection_does_not_apply():
    """SelfPickup/NovaPoshta are supported as of issue #38 -- this guard now
    only covers a genuinely unsupported type (e.g. DeliveryExpressByPromise,
    which needs yet another address-construction rule per the tool's own
    description and isn't handled by this module)."""
    responses = _base_responses(
        silpo_get_available_delivery_types=delivery_types_response(
            {"deliveryType": "DeliveryHome", "branchId": "new-branch", "description": "Regular delivery"},
            {"deliveryType": "DeliveryExpressByPromise", "branchId": "express-branch", "description": "Express"},
        )
    )
    client = FakeClient(responses)
    log_store = FakeLogStore()
    input_fn = make_input("y", "2")  # option #2 is DeliveryExpressByPromise

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False
    assert all(call[0] != "silpo_get_time_slots" for call in client.calls)
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)


def test_no_address_resolved_aborts_before_any_delivery_calls():
    client = FakeClient(
        {"silpo_get_my_delivery_addresses": addresses_response()}  # no saved addresses
    )
    log_store = FakeLogStore()
    input_fn = make_input("")  # blank -> no new address entered, resolve_address returns None

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False
    assert all(call[0] != "silpo_get_available_delivery_types" for call in client.calls)
    assert all(call[0] != "silpo_get_time_slots" for call in client.calls)
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)


def test_missing_cart_template_aborts_before_prompting_type_or_timeslot():
    """resolve_address() itself makes one silpo_get_available_delivery_types
    call as part of its own confirmation flow (address_resolver.py, reused
    as-is) -- that call is not this module's own delivery-type listing, so
    it's expected here. What must NOT happen once the cart-context guard
    fails is this module's own timeslot listing or the update call."""
    responses = _base_responses(
        silpo_get_shopping_cart_by_id=[cart_by_id_response(address=None, shipments=[])]
    )
    client = FakeClient(responses)
    log_store = FakeLogStore()
    input_fn = make_input("y")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False
    assert all(call[0] != "silpo_get_time_slots" for call in client.calls)
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)


def test_update_call_failure_does_not_report_success_or_run_post_check():
    responses = _base_responses(silpo_update_shopping_cart={"success": False})
    client = FakeClient(responses)
    log_store = FakeLogStore()
    input_fn = make_input("y", "1", "1")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False
    assert result.newly_unavailable == []
    # Only one silpo_get_shopping_cart_by_id call: the pre-apply template. No post-apply re-resolve.
    assert [call[0] for call in client.calls].count("silpo_get_shopping_cart_by_id") == 1


def test_self_pickup_happy_path_builds_correct_address_and_applies():
    """Issue #38: SelfPickup picked from the delivery-type list ->
    silpo_list_branches(hasPickup=true) -> nearest branch to the resolved
    address (out of a page listed farthest-first, to prove the distance
    sort -- not just list order -- decides "nearest") -> real self-pickup
    address shape, live-verified 2026-08-05 (city/address field names, not
    the tool description's cityFull/addressFull)."""
    branch_far = branch("branch-far", "Харків", "вул. Далека, 99", "50.0000000000000000", "36.0000000000000000", company_id="far-company")
    branch_near = branch("branch-near", "Вінниця", "вул. Соборна, 1", "49.2500000000000000", "28.4900000000000000", company_id="pickup-company")
    responses = _base_responses(
        silpo_list_branches=branches_response(branch_far, branch_near),
    )
    client = FakeClient(responses)
    log_store = FakeLogStore()
    # address: accept first saved -> delivery type: pick #2 (SelfPickup) ->
    # branch: pick #1 (nearest, sorted client-side) -> timeslot: pick #1
    input_fn = make_input("y", "2", "1", "1")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is True
    assert result.delivery_type == "SelfPickup"
    assert ("silpo_list_branches", {"hasPickup": True}) in client.calls
    assert (
        "silpo_update_shopping_cart",
        {
            "shoppingCartId": "cart-1",
            "deliveryType": "SelfPickup",
            "timeslot": {"start": "2026-08-06T10:00:00", "end": "2026-08-06T12:00:00"},
            "address": {
                "addressType": "self-pickup",
                "city": "Вінниця",
                "locality": "вул. Соборна, 1",
                "street": "вул. Соборна, 1",
                "latitude": "49.2500000000000000",
                "longitude": "28.4900000000000000",
            },
            "shipments": [{"id": "ship-1", "companyId": "pickup-company", "branchId": "branch-near", "products": []}],
        },
    ) in client.calls
    assert (
        client.calls.count(("silpo_get_time_slots", {"branchId": "branch-near", "deliveryTypes": ["SelfPickup"]})) == 1
    )


def test_self_pickup_no_branches_available_does_not_apply():
    responses = _base_responses(silpo_list_branches=branches_response())
    client = FakeClient(responses)
    log_store = FakeLogStore()
    input_fn = make_input("y", "2")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False
    assert all(call[0] != "silpo_get_time_slots" for call in client.calls)
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)


def test_nova_poshta_happy_path_builds_correct_address_and_applies():
    """Issue #38: NovaPoshta picked from the delivery-type list -> settlement
    search -> office pick -> silpo_list_branches(hasNP=true) for the
    NP-servicing branch (live-verified 2026-08-05: exactly one branch
    nationwide has hasNP=true, so no picking needed there) -> real
    nova-poshta address shape, matching silpo_update_shopping_cart's own
    tool description field-for-field."""
    settlement = {"id": "settlement-1", "title": "Київ", "area": "Київська", "region": ""}
    office = {
        "id": "office-1",
        "title": "Відділення №1: вул. Пирогівський шлях, 135",
        "address": "Київ, Пирогівський шлях, 135",
        "type": "office",
        "number": 1,
        "status": "Working",
        "latitude": 50.354786,
        "longitude": 30.542884,
    }
    np_branch = branch("np-branch", "Київ", "просп. Бандери Степана, 36", "50.4862900000000000", "30.5218900000000000", company_id="np-company")
    responses = _base_responses(
        silpo_find_nova_poshta_settlements=settlements_response(settlement),
        silpo_find_nova_poshta_offices=offices_response(office),
        silpo_list_branches=branches_response(np_branch),
    )
    client = FakeClient(responses)
    log_store = FakeLogStore()
    # address: accept first saved -> delivery type: pick #3 (NovaPoshta) ->
    # settlement search: "Київ" -> settlement: pick #1 -> office: pick #1 -> timeslot: pick #1
    input_fn = make_input("y", "3", "Київ", "1", "1", "1")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is True
    assert result.delivery_type == "NovaPoshta"
    assert ("silpo_find_nova_poshta_settlements", {"title": "Київ"}) in client.calls
    assert ("silpo_find_nova_poshta_offices", {"settlementId": "settlement-1"}) in client.calls
    assert ("silpo_list_branches", {"hasNP": True}) in client.calls
    assert (
        "silpo_update_shopping_cart",
        {
            "shoppingCartId": "cart-1",
            "deliveryType": "NovaPoshta",
            "timeslot": {"start": "2026-08-06T10:00:00", "end": "2026-08-06T12:00:00"},
            "address": {
                "addressType": "nova-poshta",
                "city": "Київ",
                "region": "Київська",
                "latitude": "50.354786",
                "longitude": "30.542884",
                "officeId": "office-1",
                "street": "Відділення #1",
            },
            "shipments": [{"id": "ship-1", "companyId": "np-company", "branchId": "np-branch", "products": []}],
        },
    ) in client.calls
    assert (
        client.calls.count(("silpo_get_time_slots", {"branchId": "np-branch", "deliveryTypes": ["NovaPoshta"]})) == 1
    )


def test_nova_poshta_no_settlements_found_does_not_apply():
    responses = _base_responses(silpo_find_nova_poshta_settlements=settlements_response())
    client = FakeClient(responses)
    log_store = FakeLogStore()
    input_fn = make_input("y", "3", "Nowhere")

    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=lambda *a: None)

    assert result.applied is False
    assert all(call[0] != "silpo_get_time_slots" for call in client.calls)
    assert all(call[0] != "silpo_update_shopping_cart" for call in client.calls)
