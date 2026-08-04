## Problem Statement

Local state and delivery context both drift out of sync with reality, and I have no way to reset or correct either from the CLI. My substitution memory and reorder history just keeps growing — I can't start fresh if I want to. My delivery address, delivery type, and timeslot are only ever set implicitly (whatever's already on my cart, or whatever `reorder` happened to pick once); I've hit a stale timeslot on my real cart already this session (`timeslot.not_found`), and I have no command to explicitly fix that and see what it breaks. And I have loyalty coupons sitting on my account that I never see unless I go dig through the app.

## Solution

Three new subcommands:

- `clear-context` — wipes my local reorder history and substitution memory, so I can start over.
- `delivery` — lets me explicitly set my delivery address, delivery type, and timeslot together (they're one real update to Silpo, not three), then tells me which cart items are now unavailable in the new context so I know before I try to check out.
- `coupons` — shows my active loyalty coupons and what triggers each one.

## User Stories

1. As a user, I want to wipe my local reorder history and substitution memory, so that I can start fresh if I want to.
2. As a user, I want to be asked to confirm before that history is wiped, so that I don't lose it by accident.
3. As a user, I want clearing local history to never touch my real Silpo cart or my login, so that it's a purely local, low-stakes reset.
4. As a user, I want to explicitly change my delivery address, delivery type, and timeslot in one flow, so that I don't have to guess which implicit path (like re-running `reorder`) would update them.
5. As a user, I want to pick my delivery type from what's actually available at my address, so that I'm not offered options that don't apply to me.
6. As a user, I want to pick from real available timeslots, so that I don't end up with a stale or invalid one like I've already hit once.
7. As a user, I want to see which of my current cart items become unavailable after I change my delivery context, so that I know before I try to check out, not after.
8. As a user, I want that availability check to be purely informational, so that changing my delivery settings never silently swaps or removes anything from my cart on its own.
9. As a user, I want to see my active loyalty coupons and what buying-condition triggers each one, so that I know what to look for while shopping.
10. As a user, I want the coupons list to be honest about what it can't do — it can't tell me if something already in my cart or search results qualifies for a coupon, since Silpo doesn't expose that link — so that I don't assume a false connection.

## Implementation Decisions

**Modules**

- **Context Clearer** — thin. Adds a `clear()` capability to the existing Reorder Log Store that resets it to its empty state, under the same file lock already used for its other writes. `clear-context` wires this behind a confirmation prompt (same `input_fn`/`print_fn` injection pattern the rest of this codebase's interactive flows already use) — declining leaves everything untouched. Never touches the OS keyring token or the real Silpo cart.
- **Delivery Settings** — deep, the one real piece of new logic here. Orchestrates three sequential choices and one mutation: (1) resolve/confirm a delivery address, reusing the existing Address Resolver rather than duplicating its confirm/pick/new-address logic — this is the same resolver issue #29 (part of the cart/deals PRD) is already wiring into `CartContext`'s fallback path, so `delivery` and that fallback share one implementation, not two; (2) list and pick a delivery type available at that address; (3) list and pick a real timeslot at that type/branch; (4) apply all three in one `silpo_update_shopping_cart` call, constructing the `shipments`/`address` objects per the real, delivery-type-specific rules that tool requires (home delivery, self-pickup, and Nova Poshta each need differently-shaped address objects — see the live-verified notes for this tool). After applying, re-resolve cart context and report which current cart items are now out of stock/unavailable in the new context — informational only, matching this project's established principle (from the cart/deals PRD) that nothing swaps or changes automatically without the user acting on it separately.
- **Coupons Lister** — thin. Calls the coupons list and per-coupon detail calls, formats each into a description + condition + reward. Explicitly does not attempt to match a coupon's free-text condition against cart contents or search results — confirmed no coupon exposes a linked product id, only descriptive text, so any attempted match would be the same unreliable keyword-guessing already rejected for promo alternatives in the cart/deals PRD.
- **CLI orchestrator** — adds `clear-context`, `delivery`, and `coupons` as new top-level subcommands alongside the existing ones.

**Architectural decisions**

- `delivery` reuses the existing Address Resolver rather than re-implementing address confirmation — one implementation, two consumers (this command, and the cart-context fallback from issue #29).
- Nothing in this PRD auto-applies a coupon, auto-matches a coupon to cart contents, or auto-substitutes a cart item after a delivery-context change — every one of these three capabilities is either a pure local reset or purely informational, consistent with the write-once-mutate-only-on-explicit-request pattern already established by `reorder` and the cart/deals PRD.
- `delivery` is interactive-only for this PRD — no non-interactive flags. Scripting it can be a follow-up if it turns out to be needed.

## Testing Decisions

Same discipline as the rest of this codebase: fixture-driven tests against the real, live-verified schemas, no live network calls.

All three modules get tests:

- **Context Clearer** — clearing resets to the empty state; declining the confirmation leaves existing history/substitution data untouched; clearing an already-empty store doesn't error.
- **Delivery Settings** — the three-step selection flow produces the correct `silpo_update_shopping_cart` payload for each delivery type's differently-shaped address object; the post-apply availability report correctly identifies newly-unavailable cart items; the report is genuinely informational (no mutating call fires from the report step itself); an invalid/no-op selection at any of the three steps fails clearly without partially applying the others.
- **Coupons Lister** — formats a list of active coupons with description/condition/reward correctly; an account with no active coupons returns/prints empty rather than erroring.

## Out of Scope

- Non-interactive flags for `delivery` (e.g. scripted address/type/timeslot selection) — may follow up later if needed.
- Any attempt to match a coupon's condition text against cart contents, search results, or promo alternatives — no reliable product linkage exists in the API for this.
- Applying, activating, or redeeming a coupon — no MCP tool exists for this; coupons apply automatically server-side when their condition is met by a real purchase.
- Automatic cart changes in response to a delivery-context change (item removal, substitution, etc.) — `delivery`'s post-apply check is informational only.
- Clearing the OS keyring auth token or the real Silpo cart from `clear-context` — that command is scoped to local history/substitution memory only.

## Further Notes

- `delivery`'s per-delivery-type address construction should be spot-checked live for each type this project actually plans to support (`DeliveryHome` confirmed in earlier live testing; `SelfPickup`/`NovaPoshta` construction rules are documented from the tool's own description but haven't been exercised live yet).
- Once built, extend `docs/mcp_schema.md` with a "Live-verified: delivery-settings and coupons tools" section, following this project's established convention.
