"""Promo Optimizer: opt-in module (`--optimize promos`, see cli.py), invoked
only when the flag is passed, per PRD's Promo Optimizer section and
CONTEXT.md's "Promo optimization" glossary entry. Two independent actions on
the Substitution Resolver's resolved item set:

(a) swap a typical item for a cheaper promotional equivalent, even when the
    original is in stock;
(b) apply available loyalty bonuses/promo codes to the cart as a whole via
    `silpo_update_shopping_cart`.

Off by default: `cli.py` only imports/calls this module when
`--optimize promos` is passed, so a plain `reorder` makes zero calls to
either tool below (verified in test_cli.py's flag-off test).

Schema assumptions (unverified live, see ../../docs/mcp_schema.md):
- A promo-equivalent lookup tool `silpo_get_promo_equivalent` taking
  `{"product_id": ...}` and returning either a falsy value (no promo
  equivalent) or a single dict `{"product_id": ..., "price": ...}`. Swap
  only applies when the returned price is strictly cheaper than the
  original item's last known price; unlike `silpo_get_replacements`, this
  isn't a user-facing choice, so no list-of-candidates handling is needed.
- A bonuses/promo-codes listing tool `silpo_get_available_bonuses` taking no
  arguments and returning a list of dicts shaped like `{"id": ...}` (or a
  bare id string). Empty/absent means nothing to apply, so
  `silpo_update_shopping_cart` is not called at all in that case.
- `silpo_update_shopping_cart` request shape: assumed to accept
  `{"bonus_ids": [...]}` — applying bonuses/promo codes to the cart as a
  whole, not per-item, per the PRD's Promo Optimizer wording.
"""

from dataclasses import dataclass, field

from silpo_agent.order_aggregator import TypicalItem


@dataclass(frozen=True)
class PromoResult:
    items: list[TypicalItem]
    swaps: list[tuple[str, str]] = field(default_factory=list)
    bonuses_applied: list[str] = field(default_factory=list)


def _cheaper_promo_item(client, item: TypicalItem) -> TypicalItem | None:
    equivalent = client.call("silpo_get_promo_equivalent", {"product_id": item.product_id})
    if not equivalent:
        return None
    price = equivalent.get("price")
    if price is None or price >= item.last_known_price:
        return None
    return TypicalItem(product_id=equivalent["product_id"], frequency=item.frequency, last_known_price=price)


def optimize_promos(client, items: list[TypicalItem]) -> PromoResult:
    resolved_items: list[TypicalItem] = []
    swaps: list[tuple[str, str]] = []

    for item in items:
        promo_item = _cheaper_promo_item(client, item)
        if promo_item is None:
            resolved_items.append(item)
        else:
            resolved_items.append(promo_item)
            swaps.append((item.product_id, promo_item.product_id))

    bonuses = client.call("silpo_get_available_bonuses") or []
    bonus_ids = [bonus.get("id") if isinstance(bonus, dict) else bonus for bonus in bonuses]
    if bonus_ids:
        client.call("silpo_update_shopping_cart", {"bonus_ids": bonus_ids})

    return PromoResult(items=resolved_items, swaps=swaps, bonuses_applied=bonus_ids)
