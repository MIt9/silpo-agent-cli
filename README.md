# silpo-agent-cli

[![Tests](https://github.com/MIt9/silpo-agent-cli/actions/workflows/test.yml/badge.svg)](https://github.com/MIt9/silpo-agent-cli/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/silpo-agent-cli.svg)](https://pypi.org/project/silpo-agent-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](pyproject.toml)

CLI wrapper over the Silpo MCP server (`https://mcp.silpo.ua/mcp`) for
grocery shopping: rebuild your cart from what you typically buy, check and
edit what's actually in it, and see what's on sale — without leaving the
terminal.

**Primary use case: pair it with an AI agent.** This is a plain,
scriptable CLI on purpose — every command has a stable `--help`, flags do
one thing, and output carries the product slugs needed for follow-up calls.
Point an agent (e.g. Claude Code) at `.claude/skills/silpo-cli-usage/` and
it can turn "reorder my usual groceries, budget 1500" into the right
invocation, read the result, and handle prompts — you don't have to
memorize flags or babysit the terminal yourself. See
[Claude Code skills](#claude-code-skills) below.

## Setup

```bash
uvx --from silpo-agent-cli silpo-agent   # run without installing
pipx install silpo-agent-cli             # or install the `silpo-agent` command
```

Developing this repo instead of just using it? Clone it and run `uv sync`
(see [CONTRIBUTING.md](CONTRIBUTING.md)); use `uv run silpo-agent` in place
of `silpo-agent` below.

First run of any command that talks to the MCP server opens a browser for a
one-time OAuth2.1+PKCE login; the token is cached in the OS keyring
afterward, so you won't be re-prompted until it expires.

## Commands

Run `silpo-agent --help` for the full list, or `<command> --help`
for a command's flags and examples. Every command is interactive where it
matters (address/item confirmation) — run them somewhere you're watching,
not backgrounded.

### `reorder` — rebuild the cart from your typical items

```bash
silpo-agent reorder --last 10 --threshold 0.5
```

Looks at your recent online orders, works out which products you buy
consistently, checks they're still in stock, handles substitutions and
(optionally) loyalty bonuses, and fills your real Silpo cart.

- `--last N` — how many of your most recent online orders to consider.
- `--threshold T` — minimum share of those orders a product must appear in
  to count as a "typical item" (e.g. `0.5` = bought in at least half).
- `--budget UAH` — optional spend cap; trims your least-frequently-bought
  items first until the total fits. Omit it to add everything and just see
  the total.
- `--optimize promos` — opt-in; applies any available loyalty bonuses to
  the cart. Omitting this flag makes zero promo-related calls.
- `--yes` / `-y` — non-interactive: auto-confirms the proposed delivery
  address, auto-confirms adding to a non-empty cart, and auto-picks the
  first candidate on any substitution with multiple replacements. Every
  auto-answered prompt is still printed, and the report still lists the
  address used, substitutions made, and items added.

Confirms your delivery address, warns and asks before proceeding if your
cart's delivery context has a real error (e.g. a stale timeslot), warns
before touching a non-empty cart, and asks which replacement you want when
an out-of-stock item has more than one candidate. `--budget` counts what's
already in the cart (its own payable total) against the cap, not just the
new items, so reordering onto a non-empty cart can't blow past budget.

### `smart-cart` — typical items, discounted favorites, and norm top-up

```bash
silpo-agent smart-cart --last 10 --threshold 0.5
silpo-agent smart-cart --people 5 --basket-type premium --budget 1500
```

Runs the same typical-items pipeline as `reorder` (address confirmation,
substitution, non-empty-cart guard), then layers on two more sources:

1. Any of your favorited products currently on discount that aren't already
   in the resulting cart — deduplicated by product id, so a favorite that's
   also a typical item is never added twice.
2. A **norm top-up**: for any grocery category (vegetables, fruits, protein,
   dairy, grains, pantry, coffee, tea) with no real product-id overlap with
   what's already going into the cart, proposes an addition sized to
   `--people N` and `--basket-type basic|eco|premium`, shown as its own list
   with a separate `[y/N]` confirmation before joining the cart (typical
   items and favorites-deals don't get this extra gate — they're known
   purchases, norms are a guess). A few items (apples, premium-only
   berries) scale up when out of season for the current month, flagged
   with a printed note — prices always come from the real, live product
   search, never a static seasonal price.

- `--last N` / `--threshold T` — same as `reorder`. Required unless
  `--no-reorder` is set.
- `--no-reorder` — skip typical items entirely; cart built from
  favorited-on-discount + norm top-up only.
- `--people N` — household size the norm top-up scales to (default `1`).
- `--basket-type basic|eco|premium` — norm generosity (default `eco`).
- `--budget N` — like `reorder --budget`, but trims across all three
  sources in priority order when over budget: norm items drop first, then
  favorited deals, then typical items as a last resort.
- `--yes` / `-y` — same as `reorder`: non-interactive, every auto-answered
  prompt (including the norm top-up confirmation) still printed.

The report lists all three sources separately ("Added N typical item(s)" /
"Added M favorited deal(s)" / "Added K norm item(s)"), and a `--budget`
trim's "Trimmed" section is labeled by which source each dropped item came
from.

### `cart` — view, edit, and check promos on your current cart

```bash
silpo-agent cart            # read-only: items, payable total, bonus balance, validations
silpo-agent cart promos     # read-only: real promo alternatives for every item in the cart
silpo-agent cart edit       # interactive: replace one item (free-text search or promo browse)
silpo-agent cart edit --replace <old-slug> <new-slug>   # non-interactive swap
silpo-agent cart edit --add <new-slug> [--quantity N]   # non-interactive add, no swap
silpo-agent cart edit --qty <slug> <num>                # set an existing line's quantity (absolute, not a delta)
silpo-agent cart edit --remove <slug>                   # delete a line, nothing added back
```

Running `silpo-agent` with no subcommand at all is the same as `cart` — it
shows the current real cart (delivery address/type/timeslot, items, payable
total, bonus balance) instead of just printing help.

`cart edit` is the only command besides `reorder` that mutates the real
cart. `--replace` is a remove-then-add swap (Silpo has no in-place
quantity/item update); `--quantity` and `--qty`'s `<num>` both accept a
fractional number (e.g. `0.5`) for weighted products sold by kg. It
validates the replacement is resolvable, or the slug is actually a cart
line, before mutating anything, so a failed lookup or bad slug never leaves
the cart missing an item.

**Products are addressed by slug.** Every read-only command prints each
product's slug at the end of its line, and that is exactly what `--replace`
takes — copy one straight across:

```
  - Молоко «Ферма» ультрапастеризоване 2,5% x2 @ 49.9 (stock: 189)  moloko-ferma-ultrapasteryzovane-2-5-576829
```

Slugs are generated by Silpo and cannot be derived from a product name, so
always copy a printed one rather than constructing it. The old slug is
matched against your cart locally; the new one is resolved through
`silpo_get_product_details`.

### `deals` — best current discounts store-wide

```bash
silpo-agent deals --limit 10               # default 10
silpo-agent deals --category "Овочі"        # scope to one category
silpo-agent deals --list-categories         # list every real category title
```

Independent of your cart — scans active promotion categories and shows the
top discounts by percentage off. `--category` is matched against real
category titles (exact match preferred, else the shortest title containing
it) — if it falls back to that fuzzy match, the actual matched title is
printed before results so a near-miss (e.g. "Вино" matching "Виноград") is
never silent. `--list-categories` shows every real title to pick from,
read-only, no deals fetched.

### `favorites-deals` — your favorites that are currently discounted

```bash
silpo-agent favorites-deals
```

### `coupons` — your active loyalty coupons

```bash
silpo-agent coupons
```

Read-only list of what's active and what buying-condition triggers each
one. Coupons apply automatically server-side when their condition is met —
there's no "activate" step this tool can perform.

### `delivery` — set address, delivery type, and timeslot

```bash
silpo-agent delivery
```

Interactive: address (existing saved address, pick a different one, or
enter a new one) → delivery type (`DeliveryHome`, `SelfPickup`, or
`NovaPoshta`) → timeslot, applied in one real update to your account. Prints
which of your current cart items are now unavailable in the new context
afterward — informational only, nothing gets swapped automatically.

### `clear-context` — wipe local reorder history, substitution memory, and cached login

```bash
silpo-agent clear-context          # asks for confirmation first
silpo-agent clear-context --yes    # skip the confirmation prompt
```

Wipes the local Reorder Log and Substitution Memory, and clears the cached
OAuth token from your OS keyring — a full reset that also logs you out. The
next command needing a token triggers a fresh browser login. Never touches
your real Silpo cart or calls the MCP server.

## Claude Code skills

- `.claude/skills/silpo-cli-usage/` (this repo) — **the intended way to
  drive this tool day to day.** Mirrors every command's `--help` output so
  an agent can turn a plain-language ask ("reorder my usual groceries,
  budget 1500", "what's in my cart", "swap the milk for something on
  promo") into the right invocation, handle the interactive prompts, and
  know this tool's quirks (cart-only scope in `reorder`, slugs vs. product
  names, where local history lives). Ships portable — no machine-specific
  paths — so it works with `silpo-agent` installed from PyPI, no repo clone
  needed. Grab it into an agent's skills dir, or point the agent at this
  repo's copy directly.

See [CONTRIBUTING.md](CONTRIBUTING.md) if you're extending the tool itself
(TDD-first, test seams, PR expectations).

## Local state

Past `reorder` runs (items added, substitutions, confirmed address, total,
timestamp) and remembered substitution choices are logged to
`~/.silpo-agent/reorder_log.json`, append-only. Nothing here feeds back into
what counts as a "typical item" — only your confirmed online orders do.
`clear-context` wipes this file.

## Tests

```bash
uv run pytest
```

## Project docs

- `CONTEXT.md` — domain glossary (Typical item, Substitution decision,
  Cart Editor, Promo Scanner, etc.) — read this before the code if a term
  is unclear.
- The original PRDs and live-verified MCP schema notes this project was
  built from aren't in this public repo — they're working notes, not
  reference docs, and may contain incidental account details. Ask in an
  issue if you need context beyond `CONTEXT.md` and the code itself.
- Build status lives in the repo's GitHub issues (`MIt9/silpo-agent-cli`),
  not a local TODO file — every ticket that shipped is closed there, with
  the PR that implemented it linked.

## Known limitations

- Substitution Resolver's availability check searches by the typical item's
  name when known, otherwise falls back to a raw product-id UUID as the
  search query — which usually returns nothing useful. A `reorder` run
  reporting every item "unavailable" is likely this gap, not genuine
  across-the-board out-of-stock. See `docs/mcp_schema.md` (issue #18).
- `reorder --optimize promos` only applies loyalty bonuses — swapping an
  item for a cheaper promo equivalent automatically was dropped there (no
  reliable per-product "find the promo version of X" tool exists). Manual
  promo discovery is still possible via `cart promos` / `cart edit`'s
  promo-browse path, which use Silpo's own similar-products engine instead
  of a name-matching guess.
- `week` (recipe-plan-based cart) from the original idea list was never
  built — the MCP server has no recipe/meal-planning tool.
- `delivery`'s NovaPoshta branch resolution assumes exactly one servicing
  branch nationwide (true for every account tested so far); if that's ever
  false for an account, the first one found is used, with a printed note
  rather than a picker.

## License

MIT — see [LICENSE](LICENSE).
