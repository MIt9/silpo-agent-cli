"""Delivery Settings: interactive `delivery` command (issue #37, part of the
#34 PRD -- prd_delivery_context_coupons.md) that lets the user explicitly set
their delivery address, delivery type, and timeslot together in one real
`silpo_update_shopping_cart` call. Issue #37 covered `DeliveryHome` only;
issue #38 (this revision) adds `SelfPickup` and `NovaPoshta` -- each needs a
differently-shaped `address` object, per `silpo_update_shopping_cart`'s own
tool description (see docs/mcp_schema.md).

Flow:
1. Resolve/confirm a delivery address -- reuses `address_resolver.py`'s
   `resolve_address()` as-is (no duplicated confirm/pick/new-address logic;
   the same resolver issue #29 wires into `CartContext`'s fallback path).
   `resolve_address()` itself makes one `silpo_get_available_delivery_types`
   call for the resolved address's coordinates and keeps the raw response on
   `ResolvedAddress.delivery_types` (added in issue #29 for exactly this
   kind of reuse) -- this module reads that field directly rather than
   making its own, second, identical call.
2. Resolve the current `CartContext` (needed as the address/shipments
   template below), passing the already-resolved address through as
   `resolved_address=` so the no-shipments fallback (issue #29) reuses it
   instead of prompting the user a second time. Fails fast if the resolved
   context has no address/shipments -- see "Real DeliveryHome
   address/shipments construction" below for why a template is required.
3. Pick a delivery type from `resolved_address.delivery_types`'s real
   response shape, live-verified 2026-08-05 -- see docs/mcp_schema.md --
   `{"success", "summary", "options": [{"deliveryType", "branchId",
   "description"}]}`, NOT `"deliveryTypes"` as the PRD/pre-ticket docs
   assumed. `DeliveryHome`/`SelfPickup`/`NovaPoshta` are supported; anything
   else stops the flow with a clear message.
   - `DeliveryHome`: `branchId` comes straight from this option.
   - `SelfPickup`/`NovaPoshta`: this option's `branchId` is `null` (per the
     tool's own "NEXT STEPS BY TYPE" description) -- resolved in the
     type-specific branch below instead.
4. Type-specific branch/address construction (`_pick_self_pickup_branch` /
   `_pick_nova_poshta_*`, live-verified 2026-08-05 against the real MCP
   server -- see docs/mcp_schema.md's "Live-verified: SelfPickup / Nova
   Poshta address construction" section for the corrections this made to
   the tool description's field names):
   - `SelfPickup`: `silpo_list_branches(hasPickup=true)`, nearest
     `_NEAREST_PICKUP_BRANCHES` branches to the resolved address sorted by
     plain lat/lon distance (good enough for "nearest of this page"; not a
     true nearest-of-all-311 search -- see docs/mcp_schema.md), user picks
     one. Address built from that branch's real fields (`city`/`address`/
     `latitude`/`longitude` -- the tool description's `cityFull`/
     `addressFull` names don't actually exist on the live record).
   - `NovaPoshta`: `silpo_find_nova_poshta_settlements` (search by name) ->
     user picks a settlement -> `silpo_find_nova_poshta_offices` -> user
     picks an office -> `silpo_list_branches(hasNP=true)` for the
     NP-servicing branch (live-verified: exactly one branch nationwide has
     `hasNP=true`, so no picking needed there).
   - Both override `shipments[].companyId` AND `.branchId` with the chosen
     branch's own values (per the tool description's "Set shipments with
     the branch companyId + branchId") -- unlike `DeliveryHome` below, which
     keeps the existing cart shipment's `companyId`.
5. List real timeslots at that branch/type (`silpo_get_time_slots`),
   filtered to `available: true` (the tool's own description: "Only pick
   slots where available=true"), and let the user pick one.
6. Apply all three in ONE `silpo_update_shopping_cart` call.

Real DeliveryHome address/shipments construction (`silpo_update_shopping_cart`'s
own live tool description): for anything other than
`DeliveryExpressByPromise`/`NovaPoshta`/`SelfPickup`, "the address object
MUST be passed exactly as received from `silpo_get_shopping_cart_by_id` ...
Do NOT construct the address manually -- always copy it from the cart
response. The shipments array must also come from the cart response."
`address_resolver.py`'s `ResolvedAddress` only carries `id`/`label`/
`latitude`/`longitude` (a display/confirmation type, not a full postal
record) and is reused as-is per this ticket's instructions, so this module
cannot -- and per the tool's own instruction, should not -- hand-build a
brand-new postal address from it. So: `address` is copied verbatim from
`CartContext.address` (same pass-through precedent as `promo_optimizer.py`'s
issue #20 call), with only `latitude`/`longitude` overridden to the
newly-resolved address's coordinates (the one part of "a new address"
`ResolvedAddress` actually gives us, and the field that determines which
branch/company can serve the order). `shipments` is copied from
`CartContext.shipments` with each entry's `branchId` overridden to the
chosen delivery type's `branchId` (a real value returned by
`silpo_get_available_delivery_types`, not invented); `companyId` is kept
from the existing cart shipment -- an assumption (one company serves all
branches for a given account), documented in docs/mcp_schema.md's
"Assumptions made in issue #37" section, matching every live-observed
cart/product record in this project sharing one `companyId`.

A cart context with no existing `address`/`shipments` (e.g. a brand-new,
never-delivered-to account) has no template to copy from -- `delivery`
fails clearly in that case rather than guessing a full postal address from
scratch, per the same "narrow scope over an unreliable guess" precedent
issue #20 established for the promo-swap feature.

Post-apply availability report: re-resolves cart context after the update
and cross-references its `calculation.validations[]` product-level entries
(`type == "product"`, carrying `context.productId` per docs/mcp_schema.md's
live-verified validation shape) against the pre-update cart's products, by
`productId`, to report which previously-in-cart items are now flagged.
Purely informational -- print only; nothing here mutates the cart, and the
update call itself already happened before this check runs.
"""

from dataclasses import dataclass, field

from silpo_agent.address_resolver import resolve_address
from silpo_agent.cart_context import resolve_cart_context

_TARGET_DELIVERY_TYPE = "DeliveryHome"
_SUPPORTED_DELIVERY_TYPES = ("DeliveryHome", "SelfPickup", "NovaPoshta")
_NEAREST_PICKUP_BRANCHES = 5
_NP_OFFICE_TYPE_LABELS = {"office": "Відділення", "parcelLocker": "Поштомат"}


@dataclass(frozen=True)
class DeliveryResult:
    applied: bool
    delivery_type: str | None = None
    newly_unavailable: list[dict] = field(default_factory=list)


def _pick_delivery_type(resolved_address, input_fn, print_fn) -> dict | None:
    # resolved_address.delivery_types is the raw silpo_get_available_delivery_types
    # response resolve_address() already fetched for these exact coordinates
    # (issue #29) -- reuse it instead of making a second, identical call.
    response = resolved_address.delivery_types or {}
    options = response.get("options") or []
    if not options:
        print_fn("No delivery types available at this address.")
        return None

    for i, option in enumerate(options, start=1):
        print_fn(f"{i}. {option.get('deliveryType')} -- {option.get('description')}")
    choice = input_fn("Pick a delivery type by number: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(options)):
        print_fn(f"No delivery type numbered {choice!r}.")
        return None

    chosen = options[idx - 1]
    delivery_type = chosen.get("deliveryType")
    if delivery_type not in _SUPPORTED_DELIVERY_TYPES:
        print_fn(
            f"{delivery_type} isn't supported by `delivery` yet -- only "
            f"{', '.join(_SUPPORTED_DELIVERY_TYPES)} are."
        )
        return None
    # Only DeliveryHome's branchId comes from this option -- SelfPickup/
    # NovaPoshta legitimately have branchId=null here (per the tool's own
    # "NEXT STEPS BY TYPE" description) and are resolved separately below.
    if delivery_type == _TARGET_DELIVERY_TYPE and not chosen.get("branchId"):
        print_fn(f"{_TARGET_DELIVERY_TYPE} has no branch at this address.")
        return None
    return chosen


def _distance_sq(lat1, lon1, lat2, lon2) -> float:
    return (lat1 - lat2) ** 2 + (lon1 - lon2) ** 2


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_self_pickup_branch(client, resolved_address, input_fn, print_fn) -> dict | None:
    """SelfPickup branch pick (issue #38): silpo_list_branches(hasPickup=true)
    per its own tool description ("show 5 nearest branches to their location
    and let them choose"). Sorts the fetched page by plain lat/lon distance
    to the resolved address -- not a true nearest-of-all-311 search across
    every page (see docs/mcp_schema.md), good enough for picking among a
    default-sized page of real branch data. Closed (`open: false`) branches
    and branches missing coordinates (can't be meaningfully distance-ranked,
    and defaulting to (0, 0) would rank them as falsely "nearest") are
    excluded before sorting."""
    response = client.call("silpo_list_branches", {"hasPickup": True}) or {}
    branches = [b for b in (response.get("branches") or []) if b.get("open")]
    if not branches:
        print_fn("No self-pickup branches available.")
        return None

    locatable = [b for b in branches if _to_float(b.get("latitude")) is not None and _to_float(b.get("longitude")) is not None]
    if not locatable:
        print_fn("No self-pickup branches available.")
        return None

    nearest = sorted(
        locatable,
        key=lambda b: _distance_sq(
            resolved_address.latitude,
            resolved_address.longitude,
            _to_float(b.get("latitude")),
            _to_float(b.get("longitude")),
        ),
    )[:_NEAREST_PICKUP_BRANCHES]

    for i, branch in enumerate(nearest, start=1):
        print_fn(f"{i}. {branch.get('city')} -- {branch.get('address')}")
    choice = input_fn("Pick a pickup branch by number: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(nearest)):
        print_fn(f"No branch numbered {choice!r}.")
        return None
    return nearest[idx - 1]


def _self_pickup_address(branch: dict) -> dict:
    """Real SelfPickup address shape, live-verified 2026-08-05 against
    silpo_list_branches(hasPickup=true): the branch record's real field
    names are `city`/`address`, NOT `cityFull`/`addressFull` as the
    silpo_update_shopping_cart tool description's own text says -- those
    fields don't exist on the live record. See docs/mcp_schema.md."""
    return {
        "addressType": "self-pickup",
        "city": branch.get("city"),
        "locality": branch.get("address"),
        "street": branch.get("address"),
        "latitude": branch.get("latitude"),
        "longitude": branch.get("longitude"),
    }


def _pick_nova_poshta_settlement(client, input_fn, print_fn) -> dict | None:
    query = input_fn("City/settlement to search for Nova Poshta delivery: ").strip()
    if not query:
        print_fn("No settlement search entered.")
        return None
    response = client.call("silpo_find_nova_poshta_settlements", {"title": query}) or {}
    settlements = response.get("settlements") or []
    if not settlements:
        print_fn(f"No Nova Poshta settlements found for {query!r}.")
        return None

    for i, settlement in enumerate(settlements, start=1):
        print_fn(f"{i}. {settlement.get('title')} ({settlement.get('area')})")
    choice = input_fn("Pick a settlement by number: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(settlements)):
        print_fn(f"No settlement numbered {choice!r}.")
        return None
    return settlements[idx - 1]


def _pick_nova_poshta_office(client, settlement, input_fn, print_fn) -> dict | None:
    response = client.call("silpo_find_nova_poshta_offices", {"settlementId": settlement.get("id")}) or {}
    offices = response.get("offices") or []
    if not offices:
        print_fn("No Nova Poshta offices found in that settlement.")
        return None

    for i, office in enumerate(offices, start=1):
        print_fn(f"{i}. {office.get('title')}")
    choice = input_fn("Pick an office by number: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(offices)):
        print_fn(f"No office numbered {choice!r}.")
        return None
    return offices[idx - 1]


def _nova_poshta_address(settlement: dict, office: dict) -> dict:
    """Real NovaPoshta address shape, per silpo_update_shopping_cart's own
    tool description, live-verified 2026-08-05 against
    silpo_find_nova_poshta_settlements/offices: settlement.title/area and
    office.id/latitude/longitude/type/number all match the tool
    description's field names as-is (unlike SelfPickup's branch fields)."""
    label = _NP_OFFICE_TYPE_LABELS.get(office.get("type"), office.get("type"))
    return {
        "addressType": "nova-poshta",
        "city": settlement.get("title"),
        "region": settlement.get("area"),
        "latitude": str(office.get("latitude")),
        "longitude": str(office.get("longitude")),
        "officeId": office.get("id"),
        "street": f"{label} #{office.get('number')}",
    }


def _pick_nova_poshta_branch(client, print_fn) -> dict | None:
    """silpo_list_branches(hasNP=true) for the branchId/companyId to ship
    through -- live-verified 2026-08-05: exactly one branch nationwide has
    hasNP=true, so this is a lookup, not a user pick. That's an
    observation about this account, not a guarantee -- if a future account
    or a different API state ever returns more than one, don't silently
    pick the first with no trace of it: say so, so it's visible rather than
    an invisible wrong guess."""
    response = client.call("silpo_list_branches", {"hasNP": True}) or {}
    branches = response.get("branches") or []
    if not branches:
        print_fn("No Nova Poshta-servicing branch found.")
        return None
    if len(branches) > 1:
        print_fn(f"{len(branches)} Nova Poshta-servicing branches found; using {branches[0].get('city')}.")
    return branches[0]


def _pick_timeslot(client, branch_id, delivery_type, input_fn, print_fn) -> dict | None:
    response = (
        client.call("silpo_get_time_slots", {"branchId": branch_id, "deliveryTypes": [delivery_type]}) or {}
    )
    slots = [slot for slot in (response.get("slots") or []) if slot.get("available")]
    if not slots:
        print_fn("No available timeslots.")
        return None

    for i, slot in enumerate(slots, start=1):
        print_fn(f"{i}. {slot.get('start')} - {slot.get('end')}")
    choice = input_fn("Pick a timeslot by number: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(slots)):
        print_fn(f"No timeslot numbered {choice!r}.")
        return None
    return slots[idx - 1]


def _newly_unavailable(pre_products: list[dict], validations: list[dict]) -> list[dict]:
    flagged_ids = {
        validation["context"]["productId"]
        for validation in validations
        if validation.get("type") == "product"
        and isinstance(validation.get("context"), dict)
        and validation["context"].get("productId")
    }
    return [product for product in pre_products if product.get("productId") in flagged_ids]


def run_delivery_settings(client, log_store, *, input_fn=None, print_fn=None):
    input_fn = input_fn or input
    print_fn = print_fn or print

    resolved_address = resolve_address(client, log_store, input_fn=input_fn, print_fn=print_fn)
    if resolved_address is None:
        print_fn("delivery: no delivery address resolved; aborting.")
        return DeliveryResult(applied=False)
    if resolved_address.latitude is None or resolved_address.longitude is None:
        print_fn("delivery: resolved address has no coordinates; aborting.")
        return DeliveryResult(applied=False)

    # Pass the address we already resolved through so the no-shipments
    # fallback (issue #29) reuses it instead of prompting a second time.
    cart_context = resolve_cart_context(client, resolved_address=resolved_address, print_fn=print_fn)
    if not (cart_context.shopping_cart_id and cart_context.address and cart_context.shipments):
        print_fn("delivery: no existing cart address/shipments to update from; aborting.")
        return DeliveryResult(applied=False)

    chosen_type = _pick_delivery_type(resolved_address, input_fn, print_fn)
    if chosen_type is None:
        return DeliveryResult(applied=False)
    delivery_type = chosen_type["deliveryType"]

    if delivery_type == _TARGET_DELIVERY_TYPE:
        branch_id = chosen_type["branchId"]
        address = dict(cart_context.address)
        address["latitude"] = str(resolved_address.latitude)
        address["longitude"] = str(resolved_address.longitude)
        shipments = [{**shipment, "branchId": branch_id} for shipment in cart_context.shipments]
    elif delivery_type == "SelfPickup":
        branch = _pick_self_pickup_branch(client, resolved_address, input_fn, print_fn)
        if branch is None:
            return DeliveryResult(applied=False)
        branch_id = branch.get("branchId")
        address = _self_pickup_address(branch)
        shipments = [
            {**shipment, "companyId": branch.get("companyId"), "branchId": branch_id}
            for shipment in cart_context.shipments
        ]
    else:  # NovaPoshta
        settlement = _pick_nova_poshta_settlement(client, input_fn, print_fn)
        if settlement is None:
            return DeliveryResult(applied=False)
        office = _pick_nova_poshta_office(client, settlement, input_fn, print_fn)
        if office is None:
            return DeliveryResult(applied=False)
        np_branch = _pick_nova_poshta_branch(client, print_fn)
        if np_branch is None:
            return DeliveryResult(applied=False)
        branch_id = np_branch.get("branchId")
        address = _nova_poshta_address(settlement, office)
        shipments = [
            {**shipment, "companyId": np_branch.get("companyId"), "branchId": branch_id}
            for shipment in cart_context.shipments
        ]

    chosen_slot = _pick_timeslot(client, branch_id, delivery_type, input_fn, print_fn)
    if chosen_slot is None:
        return DeliveryResult(applied=False)
    if not chosen_slot.get("start") or not chosen_slot.get("end"):
        print_fn("delivery: chosen timeslot is missing start/end; aborting.")
        return DeliveryResult(applied=False)

    timeslot = {"start": chosen_slot.get("start"), "end": chosen_slot.get("end")}

    response = (
        client.call(
            "silpo_update_shopping_cart",
            {
                "shoppingCartId": cart_context.shopping_cart_id,
                "deliveryType": delivery_type,
                "timeslot": timeslot,
                "address": address,
                "shipments": shipments,
            },
        )
        or {}
    )
    if not response.get("success"):
        print_fn("delivery: failed to update delivery settings.")
        return DeliveryResult(applied=False)

    print_fn(f"Delivery settings updated: {delivery_type}, {timeslot['start']} - {timeslot['end']}.")

    new_context = resolve_cart_context(client, resolved_address=resolved_address, print_fn=print_fn)
    newly_unavailable = _newly_unavailable(cart_context.products, new_context.validations)
    if newly_unavailable:
        print_fn(f"Now unavailable ({len(newly_unavailable)}):")
        for product in newly_unavailable:
            print_fn(f"  - {product.get('name') or product.get('productId')}")

    return DeliveryResult(applied=True, delivery_type=delivery_type, newly_unavailable=newly_unavailable)
