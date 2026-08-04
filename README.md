# silpo-agent-cli

Personal CLI wrapper over the Silpo MCP server (`https://mcp.silpo.ua/mcp`)
that rebuilds your Silpo cart from what you typically buy, instead of
re-typing the same grocery list every week.

One command today: `reorder`. It looks at your recent online orders, works
out which products you buy consistently, checks they're still in stock,
handles substitutions and (optionally) loyalty bonuses, and fills your real
Silpo cart. It never checks out or pays — that stays a manual step in the
Silpo app or on silpo.ua.

## Setup

```bash
uv sync
```

First run of any command that talks to the MCP server opens a browser for a
one-time OAuth2.1+PKCE login; the token is cached in the OS keyring
afterward, so you won't be re-prompted until it expires.

## Usage

```bash
uv run silpo-agent reorder --last 10 --threshold 0.5
```

- `--last N` — how many of your most recent online orders to consider.
- `--threshold T` — minimum share of those orders a product must appear in
  to count as a "typical item" (e.g. `0.5` = bought in at least half).
- `--budget UAH` — optional spend cap; trims your least-frequently-bought
  items first until the total fits. Omit it to add everything and just see
  the total.
- `--optimize promos` — opt-in; applies any available loyalty bonuses to
  the cart. Omitting this flag makes zero promo-related calls.

Run `uv run silpo-agent reorder --help` for the full step-by-step pipeline
description and more examples.

`reorder` is interactive — it'll ask you to confirm your delivery address,
warn before touching a non-empty cart, and ask which replacement you want
when an out-of-stock item has more than one candidate. Run it somewhere
you're watching, not backgrounded.

## Claude Code skill

If you drive this tool through Claude Code, there's a personal skill at
`~/.agents/skills/silpo-agent/` that teaches Claude how to translate a
request like "reorder my usual groceries, budget 1500" into the right
invocation, and about this tool's quirks (interactive prompts, cart-only
scope, where the local history lives). It's not scoped to this repo — it's
installed for this user across projects.

## Local state

Past runs (items added, substitutions, confirmed address, total, timestamp)
and remembered substitution choices are logged to
`~/.silpo-agent/reorder_log.json`, append-only. Nothing here feeds back into
what counts as a "typical item" — only your confirmed online orders do.

## Tests

```bash
uv run pytest
```

## Project docs

- `CONTEXT.md` — domain glossary (Typical item, Substitution decision,
  Reorder flow scope, etc.) — read this before the code if a term is
  unclear.
- `prd_reorder_optimizer.md` — the original PRD this was built from.
- `docs/mcp_schema.md` — live-verified schema notes for every Silpo MCP
  tool this project touches, including known gaps and assumptions (e.g.
  Substitution Resolver's availability-check limitation).
- `TODO.md` — module-by-module build status.
- `idea.md` — the original brainstorm this project narrowed down from.

## Known limitations

- Substitution Resolver's availability check searches by the typical item's
  name when known, otherwise falls back to a raw product-id UUID as the
  search query — which usually returns nothing useful. A run reporting
  every item "unavailable" is likely this gap, not genuine across-the-board
  out-of-stock. See `docs/mcp_schema.md` (issue #18) for detail.
- Promo Optimizer only applies loyalty bonuses — the original idea of
  swapping an item for a cheaper promo equivalent was dropped (no reliable
  per-product "find the promo version of X" tool exists on the live
  server; see `docs/mcp_schema.md`, issue #20).
- `week` (recipe-plan-based cart) from the original idea list was never
  built — the MCP server has no recipe/meal-planning tool.
