---
name: silpo-cli-usage
description: How to use the built silpo-agent CLI to place/rebuild a Silpo grocery order and manage cart/delivery/coupons/deals -- as a user of the finished tool, not a developer of it. Use whenever asked to reorder groceries, fill/check the cart, edit a cart item, set delivery address/type/timeslot, list coupons, or show deals. For developing/extending this repo, use the silpo-agent-cli skill instead.
---

# silpo-agent CLI usage

Source of truth is the CLI's own `--help` -- this skill mirrors it. If
flags look off from what's written here, re-run `--help` and trust that,
not this file.

Run from this repo's root (wherever it's cloned):

```bash
uv run silpo-agent <command> ...
```

All commands are **interactive** (prompt on stdin) except `deals` and
`cart edit --replace`. Run interactive commands in the foreground / a tool
call the user can actually answer prompts in -- never background them.

First run ever needs a one-time OAuth browser login against
`mcp.silpo.ua`; token then caches in the OS keyring. If a run hangs with no
output, it's probably waiting on that browser tab, not crashed.

## Commands

`silpo-agent {reorder,cart,delivery,clear-context,coupons,favorites-deals,deals}`

### reorder -- place/rebuild the order

```bash
uv run silpo-agent reorder --last N --threshold 0-1 [--budget UAH] [--optimize promos]
```
- `--last N` / `--threshold 0-1` are **required**. `--threshold 0.5` = item
  must appear in at least half of the last N orders to count as "typical."
- `--budget UAH` trims least-frequent items first until total fits (omit to
  add everything and just report total).
- `--optimize promos` opt-in only; applies loyalty bonuses before checkout.
- No specifics from user → reasonable default `--last 10 --threshold 0.5`,
  and say that's what was picked.
- Flow: confirm delivery address → find typical items → check stock,
  auto-substitute (asks when >1 candidate) → optional promo optimize →
  **warns before touching a non-empty cart**, decline aborts untouched →
  optional budget trim → adds to real cart, prints report.
- Fills cart only. Never checks out or pays -- that's always manual in the
  app or on silpo.ua. If asked to "buy"/"checkout", say `reorder` can't do
  that.

### cart -- show / edit the current real cart

```bash
uv run silpo-agent cart              # read-only: items, payable total, bonus balance
uv run silpo-agent cart promos       # read-only: discounted alternatives per item
uv run silpo-agent cart edit         # interactive: replace one item
uv run silpo-agent cart edit --replace OLD_SLUG NEW_SLUG   # non-interactive
```
- Plain `cart` is the read-only check -- use this (not `reorder`) when the
  user just wants to know what's in the cart, since `reorder` mutates it.
  Payable total is `totalAfterDiscounts`, not the pre-discount total.
- `cart edit` interactive: lists cart items → pick one → find replacement
  by free-text search or promo alternatives (`cart promos` reused) →
  confirm swap → old item removed only after new one confirmed to exist.
- `cart edit --replace` slugs must come from real output (`cart`, `deals`,
  `favorites-deals`, `cart promos`) -- never construct a slug from a
  product name, Silpo generates them.

### delivery -- set address, delivery type, timeslot

```bash
uv run silpo-agent delivery
```
Interactive only, no flags. Confirms address → pick delivery type
(DeliveryHome, SelfPickup, NovaPoshta supported; anything else stops
without changes) → for SelfPickup picks nearest branch, for NovaPoshta
searches settlement then office/locker → pick real timeslot → applies all
three together in one cart update → reports any cart items now unavailable
under the new delivery context (informational only, nothing auto-removed).

### coupons / favorites-deals / deals -- read-only discovery

```bash
uv run silpo-agent coupons            # active loyalty coupons: condition, validity, reward
uv run silpo-agent favorites-deals    # own favorites currently discounted
uv run silpo-agent deals [--limit N]  # store-wide biggest discounts, default limit 10
```
None of these touch the cart or account.

### clear-context -- wipe local state

```bash
uv run silpo-agent clear-context
```
Deletes local Reorder Log + Substitution Memory
(`~/.silpo-agent/reorder_log.json`) after confirmation. No MCP calls --
never touches the OS keyring token or the real Silpo cart.

## Past runs / substitution memory

`~/.silpo-agent/reorder_log.json` is the append-only source of truth for
"what happened last reorder" -- read it, don't infer from conversation
history.

## Known gap

Substitution stock check can false-negative: when order history lacks an
item's name, it searches by raw product-id UUID, which usually returns
nothing -- so an all-"unavailable" result is likely this gap, not real
across-the-board stock-outs (see repo's `docs/mcp_schema.md`, issue #18).
