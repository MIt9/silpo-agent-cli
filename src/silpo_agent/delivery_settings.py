"""Delivery Settings: interactive `delivery` command (issue #37, part of the
#34 PRD -- prd_delivery_context_coupons.md) that lets the user explicitly set
their delivery address, delivery type, and timeslot together in one real
`silpo_update_shopping_cart` call, for `DeliveryHome` only (other delivery
types are issue #38's scope, see the guard in `_pick_delivery_type` below).

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
   assumed. Anything other than `DeliveryHome` stops the flow with a clear
   message (SelfPickup/NovaPoshta need differently-shaped address objects
   per the tool's own description -- #38's scope, not guessed here).
4. List real timeslots at that branch/type (`silpo_get_time_slots`),
   filtered to `available: true` (the tool's own description: "Only pick
   slots where available=true"), and let the user pick one.
5. Apply all three in ONE `silpo_update_shopping_cart` call.

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
    if chosen.get("deliveryType") != _TARGET_DELIVERY_TYPE:
        print_fn(
            f"{chosen.get('deliveryType')} isn't supported by `delivery` yet -- only "
            f"{_TARGET_DELIVERY_TYPE} is (see issue #38 for other delivery types)."
        )
        return None
    if not chosen.get("branchId"):
        print_fn(f"{_TARGET_DELIVERY_TYPE} has no branch at this address.")
        return None
    return chosen


def _pick_timeslot(client, branch_id, input_fn, print_fn) -> dict | None:
    response = (
        client.call("silpo_get_time_slots", {"branchId": branch_id, "deliveryTypes": [_TARGET_DELIVERY_TYPE]}) or {}
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

    chosen_slot = _pick_timeslot(client, chosen_type["branchId"], input_fn, print_fn)
    if chosen_slot is None:
        return DeliveryResult(applied=False)
    if not chosen_slot.get("start") or not chosen_slot.get("end"):
        print_fn("delivery: chosen timeslot is missing start/end; aborting.")
        return DeliveryResult(applied=False)

    address = dict(cart_context.address)
    address["latitude"] = str(resolved_address.latitude)
    address["longitude"] = str(resolved_address.longitude)
    shipments = [{**shipment, "branchId": chosen_type["branchId"]} for shipment in cart_context.shipments]
    timeslot = {"start": chosen_slot.get("start"), "end": chosen_slot.get("end")}

    response = (
        client.call(
            "silpo_update_shopping_cart",
            {
                "shoppingCartId": cart_context.shopping_cart_id,
                "deliveryType": _TARGET_DELIVERY_TYPE,
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

    print_fn(f"Delivery settings updated: {_TARGET_DELIVERY_TYPE}, {timeslot['start']} - {timeslot['end']}.")

    new_context = resolve_cart_context(client, resolved_address=resolved_address, print_fn=print_fn)
    newly_unavailable = _newly_unavailable(cart_context.products, new_context.validations)
    if newly_unavailable:
        print_fn(f"Now unavailable ({len(newly_unavailable)}):")
        for product in newly_unavailable:
            print_fn(f"  - {product.get('name') or product.get('productId')}")

    return DeliveryResult(applied=True, delivery_type=_TARGET_DELIVERY_TYPE, newly_unavailable=newly_unavailable)
