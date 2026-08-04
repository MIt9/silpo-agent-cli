"""Address Resolver: resolves which delivery address/branch context product
search runs against, before any product search happens (see
prd_reorder_optimizer.md's Address Resolver section and CONTEXT.md's
"Delivery address resolution" entry).

Source is `silpo_get_my_delivery_addresses` (never order data). The
MCP-marked default/first address is always proposed for confirmation first,
every run, regardless of what a prior run's Reorder Log recorded. On
decline: pick from the remaining saved addresses, or type a brand-new
address (geocoded via `silpo_find_address` -> `silpo_get_available_delivery_types`).
The confirmed choice is written to the Reorder Log for audit only -- it
never feeds back into what's proposed next run.

Schema assumptions (unverified live, see ../../docs/mcp_schema.md):
each address entry is a dict optionally with "is_default" (bool) and
"address" and/or "id". `silpo_find_address` is assumed to return a list of
matches shaped like the same address dicts (or a single dict for one match).
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ResolvedAddress:
    id: str | None
    label: str


def _to_resolved(address: dict) -> ResolvedAddress:
    label = address.get("address") or address.get("label") or address.get("id")
    return ResolvedAddress(id=address.get("id"), label=label)


def _pick_from_list_or_new(remaining: list[dict], client, input_fn, print_fn) -> ResolvedAddress | None:
    for i, address in enumerate(remaining, start=1):
        print_fn(f"{i}. {_to_resolved(address).label}")
    choice = input_fn("Pick a number, or type a new address: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(remaining):
        return _to_resolved(remaining[int(choice) - 1])
    return _enter_new_address(client, choice, print_fn)


def _enter_new_address(client, query: str, print_fn) -> ResolvedAddress | None:
    query = query.strip()
    if not query:
        print_fn("No address entered.")
        return None
    found = client.call("silpo_find_address", {"query": query}) or []
    if isinstance(found, dict):
        found = [found]
    if not found:
        print_fn(f"No address found for '{query}'.")
        return None
    resolved = _to_resolved(found[0])
    client.call("silpo_get_available_delivery_types", {"address_id": resolved.id})
    return resolved


def resolve_address(client, log_store, *, input_fn=None, print_fn=None) -> ResolvedAddress | None:
    input_fn = input_fn or input
    print_fn = print_fn or print

    addresses = client.call("silpo_get_my_delivery_addresses") or []
    if addresses:
        default = next((a for a in addresses if a.get("is_default")), addresses[0])
        default_resolved = _to_resolved(default)
        answer = input_fn(f"Deliver to {default_resolved.label}? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            resolved = default_resolved
        else:
            remaining = [a for a in addresses if a is not default]
            resolved = _pick_from_list_or_new(remaining, client, input_fn, print_fn)
    else:
        print_fn("No saved delivery addresses found.")
        query = input_fn("Enter a delivery address: ")
        resolved = _enter_new_address(client, query, print_fn)

    if resolved is not None:
        log_store.append_run(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "address": resolved.label,
                "address_id": resolved.id,
            }
        )
    return resolved
