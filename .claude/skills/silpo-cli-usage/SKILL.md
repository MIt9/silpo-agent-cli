---
name: silpo-cli-usage
description: How to use the built silpo-agent CLI to place/rebuild a Silpo grocery order and manage cart/delivery/coupons/deals -- as a user of the finished tool, not a developer of it. Use whenever asked to reorder groceries, fill/check the cart, edit a cart item, set delivery address/type/timeslot, list coupons, or show deals. For developing/extending this repo, use the silpo-agent-cli skill instead.
---

# silpo-agent CLI usage

Source of truth is the CLI's own `--help` -- this skill mirrors it. If
flags look off from what's written here, re-run `--help` and trust that,
not this file.

Installed from PyPI (`pipx install silpo-agent-cli` / `uvx --from
silpo-agent-cli silpo-agent`), so it's just:

```bash
silpo-agent <command> ...
```

Working from a clone of this repo instead (dev mode)? Use `uv run
silpo-agent <command> ...` instead.

All commands are **interactive** (prompt on stdin) except `deals` and
`cart edit --replace`/`cart edit --add`. Run interactive commands in the
foreground / a tool call the user can actually answer prompts in -- never
background them.

First run ever needs a one-time OAuth browser login against
`mcp.silpo.ua`; token then caches in the OS keyring. If a run hangs with no
output, it's probably waiting on that browser tab, not crashed.

## Commands

`silpo-agent {reorder,smart-cart,cart,delivery,clear-context,coupons,favorites-deals,deals}`

### reorder -- place/rebuild the order

```bash
silpo-agent reorder --last N --threshold 0-1 [--budget UAH] [--optimize promos] [--yes]
```
- `--last N` / `--threshold 0-1` are **required**. `--threshold 0.5` = item
  must appear in at least half of the last N orders to count as "typical."
- `--budget UAH` trims least-frequent items first until total fits (omit to
  add everything and just report total). Counts what's already in the cart
  (its own payable total) against the cap, not just the new items.
- `--optimize promos` opt-in only; applies loyalty bonuses before checkout.
- `--yes` / `-y` -- non-interactive: auto-confirms the proposed address,
  auto-rolls a stale timeslot to the nearest slot when that's the only
  problem, auto-confirms adding to a non-empty cart, and auto-picks the
  first candidate on multi-candidate substitutions. Every auto-answered
  prompt is still printed, and the report still lists what was decided --
  nothing silent. Use this by default when running from an agent/chat
  context, since a bare interactive `reorder` just hangs waiting on stdin.
  `--yes` only answers yes/no and numbered-pick prompts -- if it hits a
  free-text one (no saved delivery addresses at all), it aborts loudly
  (`--yes: cannot auto-answer prompt: ...`, exit 1) instead of guessing.
- No specifics from user → reasonable default `--last 10 --threshold 0.5`,
  and say that's what was picked.
- Flow: confirm delivery address → resolve cart context, print
  address/delivery type/timeslot → **if a stale timeslot
  (`timeslot.not_found`) is the *only* error-level validation, offers to
  roll it to the nearest available slot** (`Stale timeslot -- use nearest
  available? [Y/n]`, default yes; automatic under `--yes`), then re-resolves
  and continues → **otherwise, if the cart has an error-level validation,
  warns and asks to continue -- decline aborts before any product search**,
  same as the non-empty-cart guard below → find typical items → check
  stock, auto-substitute (asks when >1 candidate) → optional promo optimize
  → **warns before touching a non-empty cart**, decline aborts untouched →
  optional budget trim → adds to real cart, prints report.
- The stale-timeslot roll above means `reorder --yes` now self-heals the
  most common stale-context case. If the roll fails (no slots) or there are
  *other* errors too, you still fall through to the plain continue-anyway
  gate. If the delivery info printed up front looks wrong in some other way,
  run `silpo-agent delivery` first instead of pushing through (or
  `silpo-agent delivery --keep-address` / `--keep-address --yes` when only
  the timeslot is stale and the address is still right).
- Fills cart only. Never checks out or pays -- that's always manual in the
  app or on silpo.ua. If asked to "buy"/"checkout", say `reorder` can't do
  that.

### smart-cart -- reorder, plus discounted favorites and a norm top-up

```bash
silpo-agent smart-cart --last N --threshold 0-1 [--people N] [--basket-type basic|eco|premium] [--budget UAH | --fill-to UAH] [--yes]
silpo-agent smart-cart --no-reorder [--people N] [--basket-type basic|eco|premium] [--budget UAH | --fill-to UAH] [--yes]
```
- `--no-reorder` skips typical items (order history) entirely -- cart built
  from favorited-on-discount + norm top-up only. `--last`/`--threshold`
  aren't needed with this (normally required).
- Same pipeline as `reorder` (address confirmation, delivery-context
  validation guard, typical items, substitution, non-empty-cart guard),
  plus two more sources layered on top, in order:
  1. Any favorited product currently on discount not already in the
     resulting cart, deduplicated by product id -- a favorite that's also a
     typical item is added once, not twice.
  2. **Norm top-up**: for any grocery category (vegetables, fruits,
     protein, dairy, grains, pantry, coffee, tea) with no real product-id
     overlap with what's already going into the cart, proposes the top
     search result for that category's staple, sized to `--people N`
     (default 1) × `--basket-type basic|eco|premium` (default `eco`),
     rounded to a valid add-to-basket step -- for a category-tag/product
     unit mismatch (a kg/l norm target vs a "шт"/piece-sold product), no
     kg-to-pack conversion is possible without the pack's real net weight,
     so it adds 1 unit with a printed note instead of guessing. Shown as
     its own list with a **separate `[y/N]` confirmation** before joining
     the cart -- unlike
     typical items/favorites-deals, which don't get this extra gate since
     they're known purchases, not guesses.
- `--last N` / `--threshold 0-1` are **required**, same meaning as
  `reorder`.
- `--budget UAH` trims across all three sources when over budget, in
  priority order: norm top-up items drop first (guesses), then favorited
  deals, then typical items as the last resort -- same
  lowest-frequency-first tie-break within a source that `reorder --budget`
  uses. Counts what's already in the cart against the cap, same as
  `reorder`.
- `--fill-to UAH` is the opposite of `--budget` (mutually exclusive with
  it): tops the cart UP toward a target spend after the three sources
  settle. Greedily fills from a priority pool -- the user's favorited
  products first (at any price), then the biggest store-wide deals -- taking
  each only if it still fits under the target and isn't already in the cart
  or a pending add. Shown as their own list ("Filling toward N:", each line
  tagged `[fav]`/`[deal]`) with a single `[y/N]` gate, then folded into the
  same one cart write. Prints how close it got if the pool runs out first.
- `--yes` / `-y` -- same as `reorder`, plus auto-confirms the norm top-up's
  and `--fill-to`'s own prompts.
- The report lists each source separately ("Added N typical item(s)" /
  "Added M favorited deal(s)" / "Added K norm item(s)" / "Added J
  fill-to-budget item(s)"), and a budget trim's "Trimmed" section is
  labeled by which source each dropped item came from.
- Fills cart only, same as `reorder` -- never checks out or pays.
- Use this instead of `reorder` when the user wants their cart topped up
  toward a complete grocery basket (by household size/budget tier), not
  just their exact historical purchases.

### cart -- show / edit the current real cart

```bash
silpo-agent cart              # read-only: items, payable total, bonus balance
silpo-agent cart promos       # read-only: discounted alternatives per item
silpo-agent cart edit         # interactive: replace one item
silpo-agent cart edit --replace OLD_SLUG NEW_SLUG   # non-interactive swap
silpo-agent cart edit --add NEW_SLUG [--quantity N] # non-interactive add, no swap
silpo-agent cart edit --qty SLUG NUM                # set an existing line's quantity (absolute set, not +N)
silpo-agent cart edit --remove SLUG                 # delete a line, nothing added back
```
- Plain `cart` is the read-only check -- use this (not `reorder`) when the
  user just wants to know what's in the cart, since `reorder` mutates it.
  Payable total is `totalAfterDiscounts`, not the pre-discount total.
  Running `silpo-agent` with no subcommand at all does the same thing.
  Cart validations that are about a specific product (e.g. out of stock,
  over the stock ceiling) print that product's name and slug inline, so a
  code like `product.offer.stock.max` is never left to a bare UUID lookup.
  Weighted items (sold by kg) print their real quantity and unit (e.g.
  `1.0 кг`), not a piece count.
- `cart edit` interactive: lists cart items → pick one → find replacement
  by free-text search or promo alternatives (`cart promos` reused) →
  confirm swap → old item removed only after new one confirmed to exist.
- `cart edit --replace`/`--add`/`--qty`/`--remove` slugs must come from real
  output (`cart`, `deals`, `favorites-deals`, `cart promos`) -- never
  construct a slug from a product name, Silpo generates them. All four are
  mutually exclusive with each other.
- `--replace` needs an existing cart line to swap out. `--add` is for a
  brand-new item that isn't in the cart yet (e.g. a discovered deal) --
  quantity 1 by default, `--quantity N` for more (accepts a fractional
  number like `0.5` for weighted products). It errors instead of mutating
  anything if the slug is already a cart line (use `--replace`, `--qty`, or
  `reorder` for that instead, so quantity never silently doubles).
  `--quantity` only makes sense together with `--add`.
- `--qty SLUG NUM` changes the quantity of an item **already** in the cart
  to the absolute value `NUM` (a set, not a delta -- `--qty milk 3` always
  leaves you with 3 regardless of what was there). `NUM` accepts a
  fractional number. Errors if `SLUG` isn't already a cart line -- use
  `--add` first.
- `--remove SLUG` deletes that cart line outright, nothing added back.
  Errors if `SLUG` isn't actually in the cart.

### delivery -- set address, delivery type, timeslot

```bash
silpo-agent delivery
silpo-agent delivery --keep-address        # only re-pick the timeslot
silpo-agent delivery --keep-address --yes  # one-shot: roll stale timeslot to nearest
silpo-agent delivery --yes                 # full flow, DeliveryHome/SelfPickup only
```
Interactive. Confirms address → pick delivery type (DeliveryHome,
SelfPickup, NovaPoshta supported; anything else stops without changes) →
for SelfPickup picks nearest branch, for NovaPoshta searches settlement
then office/locker → pick real timeslot → applies all three together in
one cart update → reports any cart items now unavailable under the new
delivery context (informational only, nothing auto-removed).

`--keep-address` skips the address and delivery-type prompts entirely,
reusing the address and type already on the cart and only re-picking the
timeslot. Use it when a timeslot went stale (`timeslot.not_found`) but the
address is still right. Aborts cleanly if the cart has no existing
address/type to reuse (run plain `silpo-agent delivery` then).

`--yes` / `-y` runs the command non-interactively for the **numbered-pick**
paths only: the proposed address is confirmed and every numbered pick
(delivery type, pickup branch, timeslot) takes the first/nearest option.
It cannot answer a free-text prompt -- so `--yes` supports **DeliveryHome
and SelfPickup**, but **not** NovaPoshta (settlement search) or a
brand-new address (no saved addresses): those abort loudly
(`--yes: cannot auto-answer prompt: ...`, exit 1) -- run plain
`silpo-agent delivery` interactively for them. `delivery --keep-address
--yes` is the one-shot "roll my stale timeslot to the nearest slot" -- the
same fix `reorder`/`smart-cart` now offer inline when a stale timeslot is
the only thing wrong with the cart.

### coupons / favorites-deals / deals -- read-only discovery

```bash
silpo-agent coupons            # active loyalty coupons: condition, validity, reward
silpo-agent favorites-deals    # own favorites currently discounted
silpo-agent deals [--limit N] [--category NAME] [--list-categories]  # store-wide biggest discounts, default limit 10
```
None of these touch the cart or account.
- `--category NAME` scopes the scan to one category (e.g. `--category
  Овочі`, `--category Пиво`) instead of every active promo store-wide.
  Matched against real category titles: exact match preferred, else the
  shortest title containing it (so "Овочі" resolves to the vegetables
  category itself, not the broader "Фрукти, овочі"). If it falls back to
  that fuzzy substring match, the CLI prints which real title it actually
  matched (e.g. `matched category "Виноград" for query "Вино"`) before
  results -- never a silent near-miss. Errors clearly if nothing matches at
  all, rather than silently showing "no deals."
- `--list-categories` prints every real category title currently
  available, read-only, no deals fetched -- use it to find the exact name
  to pass to `--category` instead of guessing.
- A parent category (e.g. "Фрукти, овочі") does **not** include its child
  categories' products (e.g. "Овочі", "Фрукти") -- Silpo's category filter
  isn't recursive. If a broad category search looks thin, try the more
  specific child category by name instead.

### clear-context -- wipe local state and log out

```bash
silpo-agent clear-context          # asks for confirmation first
silpo-agent clear-context --yes    # skip the confirmation prompt
```
Deletes local Reorder Log + Substitution Memory
(`~/.silpo-agent/reorder_log.json`) *and* clears the cached OAuth token
from the OS keyring -- a full reset that also logs you out. Next command
needing a token triggers a fresh browser login. No MCP calls -- never
touches the real Silpo cart. `--yes`/`-y` skips the confirmation prompt;
without it the command hangs waiting on stdin, same reasoning as
`reorder --yes`.

## Before running reorder/smart-cart/cart edit from chat

Run read-only `silpo-agent cart` first (even right after `delivery`, in the
same conversation) and check its `Validations:` section is clean. Don't
assume a just-confirmed address/timeslot is still valid. The CLI itself now
gates on error-level validations (see "Delivery context validation guard"
above), and `reorder`/`smart-cart` will offer (or, under `--yes`,
automatically) roll a stale timeslot to the nearest slot when that's the
only problem -- but checking first still avoids running the whole pipeline
just to hit the gate, and surfaces the actual state to the user before
deciding. A stale timeslot plus any *other* error still needs a manual
`silpo-agent delivery`.

## Past runs / substitution memory

`~/.silpo-agent/reorder_log.json` is the append-only source of truth for
"what happened last reorder" -- read it, don't infer from conversation
history.

## Known gap

Substitution stock check can false-negative: when order history lacks an
item's name, it searches by raw product-id UUID, which usually returns
nothing -- so an all-"unavailable" result is likely this gap, not real
across-the-board stock-outs (see repo's `docs/mcp_schema.md`, issue #18).
