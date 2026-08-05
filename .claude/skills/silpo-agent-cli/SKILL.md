---
name: silpo-agent-cli
description: How to develop, extend, and debug this repo (silpo-agent-cli). Use this whenever the user asks to add a feature, fix a bug, pick up a GitHub issue, verify something against the live Silpo MCP server, or asks how this project's tickets/testing/review conventions work -- even if they don't name this skill explicitly, since it's the source of truth for this repo's workflow.
---

# silpo-agent-cli development

This is a personal Python CLI (`uv`-managed) wrapping the Silpo MCP server.
For what the tool *does* as a user-facing product, read `README.md` and
`CONTEXT.md` (domain glossary) first — this skill is about *building* it,
not using it. A separate personal skill (`~/.agents/skills/silpo-agent/`)
covers day-to-day usage of the finished `reorder` command.

## Read before touching code

- `CONTEXT.md` — domain glossary. Use its vocabulary in code, tests, and
  commit messages (e.g. "Typical item," "Substitution decision," not
  invented synonyms).
- `prd_reorder_optimizer.md` — the PRD this was built from: module
  boundaries, testing decisions, out-of-scope list.
- `docs/mcp_schema.md` — **the single most important file for correctness.**
  Nearly every module's first implementation guessed at MCP tool schemas
  from the PRD; live testing later found several of those guesses wrong
  (wrong field names, wrong param shapes, invented tools that don't exist).
  Every confirmed real schema and every remaining known gap/assumption is
  recorded here, dated, per-issue. Check it before assuming a tool's
  request/response shape — don't re-guess something already answered here.
- Build status lives in the repo's GitHub issues (`MIt9/silpo-agent-cli`),
  not a local TODO file — `gh issue list --repo MIt9/silpo-agent-cli` shows
  what's open/closed and which PR closed each one.

## Workflow this repo uses for new work

1. **Every ticket goes through TDD first.** Before writing implementation,
   invoke the `tdd` skill and follow its red-green process — this repo's
   existing modules were all built this way, and the pattern (tests
   colocated in `tests/`, one test module per implementation module, fixture
   data mocking `MCPClient.call` rather than hitting the network) should
   stay consistent.
2. **Real bugs get fixed directly; new capability gets a ticket.** Small,
   well-understood correctness fixes (a wrong field name, a missing guard)
   are fine to fix directly with a test, the way the OAuth/schema-envelope
   fixes were handled live in this session. Anything larger — a new module,
   a redesign, a live-schema correction spanning multiple files — gets a
   GitHub issue in `MIt9/silpo-agent-cli`, labeled `ready-for-agent`,
   referencing "Part of #1" (the parent PRD issue) and a "Blocked by"
   section naming any issues it depends on.
3. **Every non-trivial change gets an independent review pass before
   merging** — this repo has consistently used a fresh reviewer (not the
   same agent that wrote the code) to catch what the implementer missed,
   focused on the specific live-schema facts and edge cases relevant to
   that change, not a generic pass. Findings get fixed and re-reviewed
   before merge, not merged with known issues outstanding.
4. **Branch per issue, PR against `main`, "Closes #N" in the body.** Squash
   merge, delete the branch. Don't merge with unresolved review findings or
   a `CONFLICTING` merge state — rebase/resolve first.
5. After merging a module that changes MCP tool call shapes, update
   `docs/mcp_schema.md` with what was verified/assumed.

## Live testing gotchas already solved once — don't rediscover them

- **OAuth redirect_uri must use the `localhost` hostname, not the
  `127.0.0.1` IP literal** — using the raw IP got silently Cloudflare-blocked
  on `mcp.silpo.ua/authorize`; switching to `localhost` fixed it (see
  `src/silpo_agent/auth.py`'s `REDIRECT_URI` and the `mcp_auth_cloudflare_block`
  assistant memory note for the full investigation trail).
- **`MCPClient.call()`'s transport must unwrap the MCP content envelope.**
  A real `tools/call` response wraps the tool's actual JSON output as a
  string inside `result.content[0].text` — it is not already-parsed data.
  This is handled centrally in `auth.py`'s `_unwrap_tool_result()`; don't
  re-parse this envelope in individual modules.
- **MCP list-shaped tool responses are wrapped, not bare.**
  `silpo_get_my_online_orders`, `silpo_get_my_delivery_addresses`,
  `silpo_find_address`, etc. all return `{"success", "summary", "<list-key>":
  [...], ...}`, never a bare list — every call site needs to unwrap the
  named key, and every test fixture should mirror that shape (a bare-list
  fixture will pass tests while hiding a real bug — this happened once
  already with `silpo_get_my_online_orders` in `cli.py`).
- **`silpo_get_my_shopping_cart` returns only a cart id**, not contents —
  always follow with `silpo_get_shopping_cart_by_id` (see `cart_context.py`).
- **Slug is this CLI's public product identifier, not the product UUID**
  (issue #50). Every read-only command prints it and `cart edit --replace`
  takes it. A slug must be copied from a real response — `silpo_get_products`,
  `silpo_find_products_batch`, or a cart line — and can never be constructed
  from a product name; the server's own tool description says so explicitly.
  `silpo_get_product_details({branchId, slug, deliveryType, timeslotStart,
  timeslotEnd})` is the per-slug lookup, and it returns `companyId`/`branchId`
  on the record, so callers don't need `CartContext.company_id` (which is
  `None` on the #29 no-shipments path).
- Live testing this project's own auth flow requires a real Silpo account
  and a browser; if you hit an unexplained Cloudflare block or empty-looking
  response after touching `auth.py` or any live schema assumption, check
  `docs/mcp_schema.md` and the assistant's `mcp_auth_cloudflare_block` memory
  note before spending time re-diagnosing from scratch.

## Testing

```bash
uv run pytest -q
```

All tests mock `MCPClient.call` (or the whole client) with fixture data —
none hit the network. When fixing a live-schema bug, update the fixture to
the real shape (found in `docs/mcp_schema.md`) as part of the fix, not just
the implementation, so the test would have caught it.