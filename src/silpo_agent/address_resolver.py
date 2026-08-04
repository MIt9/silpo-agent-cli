"""Address Resolver: resolves which delivery address/branch context product
search runs against, before any product search happens (see
prd_reorder_optimizer.md's Address Resolver section and CONTEXT.md's
"Delivery address resolution" entry).

Source is `silpo_get_my_delivery_addresses` (never order data). The first
address in the API's return order is always proposed for confirmation first,
every run, regardless of what a prior run's Reorder Log recorded -- MCP does
not mark any address as a server-side default (live-verified, see
../../docs/mcp_schema.md's "Live-verified: address tools" section), so
"propose first" just means "first as the API returns them," documented here
as an assumption rather than a real default. On decline: pick from the
remaining saved addresses, or type a brand-new address (geocoded via
`silpo_find_address` -> `silpo_get_available_delivery_types`, looked up by
lat/lon). The confirmed choice is written to the Reorder Log for audit only
-- it never feeds back into what's proposed next run.

Real schemas (live-verified, see ../../docs/mcp_schema.md):
- `silpo_get_my_delivery_addresses` takes no args and returns
  `{"success", "summary", "addresses": [...]}`; each entry has
  `id`/`tag`/`city`/`street`/`building`/`apartment`/`floor`/`entrance`/
  `latitude`/`longitude`/`comment`, no `"address"`/`"label"`/`"is_default"`.
- `silpo_find_address` takes `{"address": "<free text>"}` and returns
  `{"success", "summary", "addresses": [...]}`; each entry has
  `address`/`city`/`street`/`houseNumber`/`district`/`latitude`/`longitude`,
  no `"id"` (it's a geocode result, not a saved-address record).
- `silpo_get_available_delivery_types` takes `{"latitude": ..., "longitude": ...}`,
  sourced from whichever resolved address's coordinates (saved or geocoded).
"""

from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True)
class ResolvedAddress:
    id: str | None
    label: str
    latitude: float | None
    longitude: float | None
    # Raw `silpo_get_available_delivery_types` response for this address's
    # coordinates, if looked up below -- `None` when coordinates were missing
    # so the lookup was skipped. Exposed so the Cart Context Resolver's
    # no-shipments fallback (issue #29) can reuse this call's result instead
    # of duplicating it.
    delivery_types: dict | None = None


def _build_label(address: dict) -> str:
    """Human-readable label from a saved address's city/street/building/apartment,
    e.g. "Вінниця, Варшавська вулиця, 27, кв. 25"."""
    parts = [address.get("city"), address.get("street"), address.get("building")]
    if address.get("apartment"):
        parts.append(f"кв. {address['apartment']}")
    return ", ".join(str(p) for p in parts if p)


def _to_resolved(address: dict) -> ResolvedAddress:
    # silpo_find_address results already carry a pre-formatted "address" string;
    # saved addresses don't, so build one from city/street/building/apartment.
    label = address.get("address") or _build_label(address)
    return ResolvedAddress(
        id=address.get("id"),
        label=label,
        latitude=address.get("latitude"),
        longitude=address.get("longitude"),
    )


def _unwrap_addresses(response) -> list[dict]:
    if isinstance(response, dict):
        return response.get("addresses") or []
    return response or []


def _pick_from_list_or_new(remaining: list[dict], client, input_fn, print_fn) -> ResolvedAddress | None:
    for i, address in enumerate(remaining, start=1):
        print_fn(f"{i}. {_to_resolved(address).label}")
    choice = input_fn("Pick a number, or type a new address: ").strip()
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(remaining):
            return _to_resolved(remaining[idx - 1])
        print_fn(f"No address numbered {idx}.")
        return None
    return _enter_new_address(client, choice, print_fn)


def _enter_new_address(client, query: str, print_fn) -> ResolvedAddress | None:
    query = query.strip()
    if not query:
        print_fn("No address entered.")
        return None
    found = _unwrap_addresses(client.call("silpo_find_address", {"address": query}))
    if not found:
        print_fn(f"No address found for '{query}'.")
        return None
    return _to_resolved(found[0])


def resolve_address(client, log_store, *, input_fn=None, print_fn=None) -> ResolvedAddress | None:
    input_fn = input_fn or input
    print_fn = print_fn or print

    addresses = _unwrap_addresses(client.call("silpo_get_my_delivery_addresses"))
    if addresses:
        first_resolved = _to_resolved(addresses[0])
        answer = input_fn(f"Deliver to {first_resolved.label}? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            resolved = first_resolved
        else:
            resolved = _pick_from_list_or_new(addresses[1:], client, input_fn, print_fn)
    else:
        print_fn("No saved delivery addresses found.")
        query = input_fn("Enter a delivery address: ")
        resolved = _enter_new_address(client, query, print_fn)

    if resolved is not None:
        # A resolved address without coordinates can't establish delivery-type
        # context (a malformed saved-address entry, e.g.) -- skip rather than
        # send nulls to the real API.
        if resolved.latitude is not None and resolved.longitude is not None:
            delivery_types = client.call(
                "silpo_get_available_delivery_types",
                {"latitude": resolved.latitude, "longitude": resolved.longitude},
            )
            resolved = replace(resolved, delivery_types=delivery_types)
        log_store.append_run(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "address": resolved.label,
                "address_id": resolved.id,
            }
        )
    return resolved
