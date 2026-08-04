"""Substitution Resolver: for each Typical Item, checks current availability
and, if unavailable, resolves a replacement via `silpo_get_replacements`
(see prd_reorder_optimizer.md's Substitution Resolver section and
CONTEXT.md's "Substitution decision" / "Substitution memory" entries).

Rules:
- Zero replacement candidates: item is reported unavailable, not added to
  the cart, and the run does not crash.
- Exactly one candidate: auto-applied, no prompt.
- More than one candidate: Substitution Memory (`ReorderLogStore.get_substitution`)
  is consulted first for a prior choice; if one exists it's auto-applied
  without asking. Otherwise the user is asked to pick, and the answer is
  persisted via `ReorderLogStore.set_substitution` for future runs.

Runs on the Order Aggregator's output, before the Cart Writer (PRD pipeline:
Address Resolver -> Order Aggregator -> Substitution Resolver -> Cart Writer).

Schema assumptions (unverified live, see ../../docs/mcp_schema.md):
- An availability check tool `silpo_check_availability` taking
  `{"product_id": ...}` and returning `{"available": bool}`.
- `silpo_get_replacements` taking `{"product_id": ...}` and returning a list
  of candidate dicts shaped like `{"product_id": ..., "price": ...}` (or a
  single dict for one candidate, normalized to a one-item list here, same
  pattern as `address_resolver`'s `silpo_find_address` handling).
"""

from dataclasses import dataclass

from silpo_agent.order_aggregator import TypicalItem


@dataclass(frozen=True)
class SubstitutionResult:
    items: list[TypicalItem]
    substitutions: list[tuple[str, str]]
    unavailable: list[str]


def _to_item(candidate: dict, frequency: float, fallback_price: float) -> TypicalItem:
    return TypicalItem(
        product_id=candidate["product_id"],
        frequency=frequency,
        last_known_price=candidate.get("price", fallback_price),
    )


def _is_available(client, product_id: str) -> bool:
    result = client.call("silpo_check_availability", {"product_id": product_id})
    return bool(result and result.get("available"))


def _resolve_one(item: TypicalItem, client, log_store, input_fn, print_fn) -> tuple[TypicalItem | None, str | None]:
    """Returns (resolved_item_or_None, substituted_from_product_id_or_None)."""
    candidates = client.call("silpo_get_replacements", {"product_id": item.product_id}) or []
    if isinstance(candidates, dict):
        candidates = [candidates]

    if not candidates:
        return None, None

    if len(candidates) == 1:
        return _to_item(candidates[0], item.frequency, item.last_known_price), item.product_id

    remembered_id = log_store.get_substitution(item.product_id)
    if remembered_id is not None:
        match = next((c for c in candidates if c.get("product_id") == remembered_id), None)
        price = match.get("price", item.last_known_price) if match else item.last_known_price
        return TypicalItem(product_id=remembered_id, frequency=item.frequency, last_known_price=price), item.product_id

    print_fn(f"{item.product_id} is unavailable. Choose a replacement:")
    for i, candidate in enumerate(candidates, start=1):
        print_fn(f"{i}. {candidate.get('product_id')}")
    choice = input_fn("Pick a number: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(candidates)):
        print_fn(f"No candidate numbered {choice!r}.")
        return None, None

    chosen = candidates[idx - 1]
    log_store.set_substitution(item.product_id, chosen["product_id"])
    return _to_item(chosen, item.frequency, item.last_known_price), item.product_id


def resolve_substitutions(
    client, log_store, typical_items: list[TypicalItem], *, input_fn=None, print_fn=None
) -> SubstitutionResult:
    input_fn = input_fn or input
    print_fn = print_fn or print

    items: list[TypicalItem] = []
    substitutions: list[tuple[str, str]] = []
    unavailable: list[str] = []

    for item in typical_items:
        if _is_available(client, item.product_id):
            items.append(item)
            continue

        resolved, original_id = _resolve_one(item, client, log_store, input_fn, print_fn)
        if resolved is None:
            unavailable.append(item.product_id)
        else:
            items.append(resolved)
            substitutions.append((original_id, resolved.product_id))

    return SubstitutionResult(items=items, substitutions=substitutions, unavailable=unavailable)
