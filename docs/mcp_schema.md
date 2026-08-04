# Silpo MCP server — live schema notes

Status as of this ticket: **auth endpoints verified live, `tools/list` schema
NOT verified** — see "What's unverified" below.

## Verified against the live server (`https://mcp.silpo.ua/mcp`)

Probed directly with `curl` (no browser/credentials needed for these):

- `POST /mcp` without a bearer token returns `401` with
  `{"error":"invalid_token","error_description":"Missing or invalid access token"}`
  and a `WWW-Authenticate: Bearer realm="OAuth", resource_metadata="https://mcp.silpo.ua/.well-known/oauth-protected-resource/mcp"` header.
- `GET /.well-known/oauth-protected-resource` →
  `{"resource":"https://mcp.silpo.ua","authorization_servers":["https://mcp.silpo.ua"],"bearer_methods_supported":["header"]}`
- `GET /.well-known/oauth-authorization-server` →
  ```json
  {
    "issuer": "https://mcp.silpo.ua",
    "authorization_endpoint": "https://mcp.silpo.ua/authorize",
    "token_endpoint": "https://mcp.silpo.ua/token",
    "registration_endpoint": "https://mcp.silpo.ua/register",
    "response_types_supported": ["code"],
    "response_modes_supported": ["query"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
    "revocation_endpoint": "https://mcp.silpo.ua/token",
    "code_challenge_methods_supported": ["plain", "S256"],
    "client_id_metadata_document_supported": false
  }
  ```
  Confirms OAuth2.1 + PKCE (S256) with dynamic client registration, matching
  `silpo_agent/auth.py`'s implementation (`_register_client`, `pkce_browser_login`).

## What's unverified

The MCP server requires a valid bearer token for **every** request,
including `tools/list` and `initialize` — there is no unauthenticated
introspection endpoint. Getting a token requires an interactive browser
login against a real Silpo account, which this ticket's sandboxed
environment cannot complete (no browser session, no Silpo credentials).

As a result, the following are **stubbed against tool/field names inferred
from the PRD and `CONTEXT.md`, not confirmed against the live `tools/list`
response**:

- Exact input/output schema of `silpo_get_my_online_orders` (order object
  shape — line items, price, paid/confirmed status, timestamp).
- Exact input/output schema of `silpo_get_my_delivery_addresses` (address
  object shape, which field marks the default).
- All other `silpo_*` tool names referenced by the PRD: `silpo_find_address`,
  `silpo_get_available_delivery_types`, `silpo_get_replacements`,
  `silpo_get_my_shopping_cart`, `silpo_get_shopping_cart_by_id`,
  `silpo_add_or_update_cart_products`, `silpo_update_shopping_cart`,
  `silpo_get_my_family`.

`MCPClient.call(tool, args)` (in `silpo_agent/auth.py`) is a thin, generic
JSON-RPC `tools/call` wrapper that doesn't hardcode any of these shapes, so
it works unchanged once real schemas are confirmed.

**Action for a later ticket**: once a human can complete the browser OAuth
flow once (`uv run silpo-agent` will trigger it on first run and cache the
token in the OS keyring), run `client.call("tools/list", {})` — or add a
one-off `silpo-agent debug-schema` command — and replace this section with
the real field names before the Order Aggregator / Address Resolver tickets
build assumptions on top of them.

## Assumptions made in issue #3 (minimal reorder happy path)

Still no live-verified schema available at implementation time, so the
`reorder` command's happy path (`silpo_agent/order_aggregator.py`,
`silpo_agent/cart_writer.py`, `silpo_agent/cli.py`) makes these additional,
narrower assumptions on top of the ones above:

- **`silpo_get_my_online_orders` order shape**: each order is a dict with an
  `"items"` list; each line item has `"product_id"` and `"price"`. The tool
  is assumed to already return only confirmed/paid orders (per the PRD's
  user story 2 and CONTEXT.md's "Reorder log" entry) — the Order Aggregator
  does not filter on a paid/confirmed status field itself.
- **Order recency ordering**: orders are assumed to come back **newest
  first**. `derive_typical_items` takes the first `last` entries as "the N
  most recent orders" and, for "last known price," keeps the price from the
  first (i.e. most recent) order it sees an item in. If the live API
  actually returns oldest-first or unordered, this needs a sort-by-timestamp
  step once the real date field name is confirmed.
- **`silpo_get_my_online_orders` call args**: called with no arguments
  (`client.call("silpo_get_my_online_orders")`); `--last`/`--threshold`
  slicing happens locally in the pure Order Aggregator, not via an API-side
  limit param. If the live tool paginates or caps results below what `--last`
  needs, this will under-count rather than error — worth revisiting once the
  real response shape (and any limit/page params) is confirmed.
- **`silpo_get_my_delivery_addresses` shape**: assumed to return a list of
  dicts, each optionally with `"is_default"` (bool) and `"address"` and/or
  `"id"`. The CLI picks the entry with `"is_default"` true, falling back to
  the first entry, and only uses it to print "Delivering to: ..." in the
  report — it is **not** passed into `silpo_add_or_update_cart_products`,
  since that call's parameter for branch/address context is unconfirmed.
  Assumed the account's currently active address is used server-side by
  default. Revisit once the real schema — and whether cart writes need an
  explicit branch/address id — is confirmed.
- **`silpo_add_or_update_cart_products` request shape**: assumed to accept
  `{"items": [{"product_id": ..., "quantity": ...}]}`. Each Typical Item is
  added at `quantity: 1` — the Order Aggregator's Typical Item (product id,
  frequency, last known price) has no usual-quantity signal, so quantity
  logic is out of scope for this ticket.
- **Report total**: computed locally as the sum of each added item's last
  known price, rather than trusting a total in
  `silpo_add_or_update_cart_products`'s response — that response shape is
  also unconfirmed.

## Assumptions made in issue #4 (delivery address confirmation flow)

Still no live-verified schema at implementation time. `silpo_agent/address_resolver.py`
replaces issue #3's silent `_resolve_default_address` with an interactive
confirm/pick/new-address flow, and makes these additional assumptions on top
of the ones above:

- **`silpo_get_my_delivery_addresses` shape** (supersedes issue #3's note):
  same assumption — a list of dicts, each optionally with `"is_default"`
  (bool), `"address"` and/or `"id"`. The Address Resolver now uses the full
  list (not just the default), so an address missing both `"address"` and
  `"id"` would display as `None` in a prompt — not defended against, since
  the real shape is unconfirmed.
- **`silpo_find_address` request/response shape**: assumed to accept
  `{"query": "<free-text the user typed>"}` and return either a list of
  address dicts (same shape as `silpo_get_my_delivery_addresses` entries —
  `"id"` / `"address"`) or a single dict for one match. The Address Resolver
  normalizes a single-dict response into a one-item list and uses the first
  result. Zero results is treated as "not found" and the run resolves no
  address for that attempt. Revisit once the real response shape (ranking,
  multiple candidates, confidence) is confirmed.
- **`silpo_get_available_delivery_types` request shape**: assumed to accept
  `{"address_id": "<id from the found address>"}`. Called immediately after
  a successful `silpo_find_address` match, per the PRD's
  `silpo_find_address` -> `silpo_get_available_delivery_types` chain, but its
  response is not inspected or acted on yet (no delivery-type selection UX
  exists) — this ticket only establishes the address/branch context, not
  delivery-slot selection. Revisit once a delivery-type step is built.
- **Reorder Log audit record shape**: `ReorderLogStore.append_run` already
  accepts an arbitrary dict (no schema enforcement), so the Address Resolver
  writes a minimal record — `{"timestamp", "address", "address_id"}` — per
  run once an address is confirmed, rather than adding a new store method.
  This is narrower than the full run record shape used in
  `test_log_store.py`'s fixtures (`items_added`, `substitutions`, `total`);
  a later ticket that logs the full reorder run may want to merge these into
  one `append_run` call per run instead of two.
- **Unresolved address hard-stops the run**: if `resolve_address` returns
  `None` (out-of-range list choice, blank/not-found new-address entry),
  `cli.py` prints an error and exits 1 *before* calling
  `silpo_get_my_online_orders` or touching the cart — same treatment as
  insufficient order history. Chosen over silently proceeding
  uncontextualized because the PRD's Address Resolver section states
  delivery/branch context determines product availability and pricing, so
  running product search without it would produce results for the wrong
  branch. Revisit only if a future ticket wants a "search anyway, ungated"
  fallback.

## Assumptions made in issue #5 (out-of-stock substitution flow with memory)

Still no live-verified schema at implementation time. `silpo_agent/substitution_resolver.py`
adds availability-checking and replacement resolution on top of the ones
above:

- **Availability check tool/shape**: no availability-check tool name is
  given anywhere in the PRD, CONTEXT.md, or this ticket's issue text — only
  "checks current availability" is specified. Assumed a tool named
  `silpo_check_availability` taking `{"product_id": "<id>"}` and returning
  `{"available": <bool>}`. This is the least-confirmed assumption in this
  ticket (an outright invented tool name, not one referenced anywhere in the
  PRD's module list) — revisit first once `tools/list` is inspected live;
  the real tool may instead be a field on the product-search response rather
  than a standalone call.
- **`silpo_get_replacements` request/response shape**: assumed to accept
  `{"product_id": "<id>"}` and return a list of candidate dicts shaped like
  `{"product_id": ..., "price": ...}` (or a single dict for one candidate,
  normalized to a one-item list — same pattern as `address_resolver`'s
  `silpo_find_address` handling). A candidate missing `"price"` falls back to
  the original Typical Item's last known price.
- **Substitution Memory key/value**: reuses `ReorderLogStore.set_substitution` /
  `get_substitution` (already built in the foundations ticket) — keyed by the
  *original* typical item's `product_id`, valued by the chosen replacement's
  `product_id`. If a later run's candidate list no longer contains the
  remembered replacement id, the remembered id is still applied (per the
  PRD's "reuse without asking"); its price falls back to the original item's
  last known price since the candidate list can't supply one.
- **Invalid user pick handling**: an out-of-range or non-numeric pick when
  asked to choose among multiple candidates is treated the same as zero
  candidates (item reported unavailable, run continues) rather than
  re-prompting or crashing — not specified by the acceptance criteria, chosen
  for consistency with `address_resolver`'s existing out-of-range handling.
- **Pipeline placement**: `cli.py` now calls `resolve_substitutions` on the
  Order Aggregator's `typical_items` output and passes its `.items` into
  `write_cart`, per the PRD order Address Resolver -> Order Aggregator ->
  Substitution Resolver -> Cart Writer. Substitution/unavailable reporting
  lines are printed before the "Added N item(s)" line.

## Assumptions made in issue #6 (non-empty cart guard and optional budget cap)

Still no live-verified schema at implementation time. `silpo_agent/cart_writer.py`
now reads the current cart before mutating it and can trim to a budget, on
top of the assumptions above:

- **`silpo_get_my_shopping_cart` request/response shape**: assumed to take no
  arguments (`client.call("silpo_get_my_shopping_cart")`) and return a dict
  with an `"items"` list — an empty or absent list means an empty cart. A
  bare list response (no wrapping dict) is also treated as the items list
  directly, mirroring the dict-or-list normalization already used for
  `silpo_find_address` / `silpo_get_replacements`. `silpo_get_shopping_cart_by_id`
  (the by-id variant named in the ticket) is not called anywhere yet — no
  cart id is available from any tool response built so far, so this ticket
  only wires the "my cart" read. Revisit once the real response shape (and
  whether a cart id needs to be threaded through from another call) is
  confirmed.
- **Non-empty cart guard UX**: on a non-empty cart, `write_cart` prints a
  warning and asks "Add to the existing cart anyway? [y/N]" via the same
  `input_fn`/`print_fn` injection pattern as `address_resolver` and
  `substitution_resolver`. A blank or non-"y" answer aborts the run *before*
  `silpo_add_or_update_cart_products` is ever called — the cart is left
  exactly as read, never auto-cleared or merged (per CONTEXT.md's "Non-empty
  cart guard" entry). `cli.py` treats an aborted `CartReport` as a hard stop
  (exit code 1), same treatment as the existing insufficient-order-history /
  unresolved-address abort paths.
- **Budget trim priority signal**: the Order Aggregator's `TypicalItem` only
  carries `frequency` as a priority signal (no explicit priority field), so
  `--budget` trims by ascending `frequency` (lowest-frequency items dropped
  first), tie-broken by `product_id` for determinism. Trimming is a simple
  greedy drop-lowest-priority-until-it-fits loop, not a knapsack-optimal
  packing — it can drop more value than strictly necessary when item prices
  don't align neatly with the cap. Revisit if the PRD ever wants an
  optimal-fit budget trim.
- **Guard vs. budget ordering**: the cart-read guard runs first (per the
  PRD's "before adding anything, reads the current cart" ordering), then the
  budget trim, then the add call — so a declined guard prompt aborts before
  any budget trimming happens.

## Assumptions made in issue #7 (promo optimization, `--optimize promos`)

Still no live-verified schema at implementation time. `silpo_agent/promo_optimizer.py`
is a new, wholly opt-in module — `cli.py` only imports/calls it when
`--optimize promos` is passed, so these two tool names are never referenced
in a plain `reorder` run:

- **Promo-equivalent lookup tool/shape**: no tool name is given anywhere in
  the PRD, CONTEXT.md, or the issue text for the "swap for a cheaper promo
  equivalent" half of promo optimization — only the behavior is specified.
  Assumed a tool named `silpo_get_promo_equivalent` taking
  `{"product_id": "<id>"}` and returning either a falsy value (no promo
  equivalent exists) or a single dict `{"product_id": ..., "price": ...}`.
  Unlike `silpo_get_replacements` (Substitution Resolver, issue #5), this is
  never a user-facing choice among multiple candidates — the PRD describes
  promo swap as an automatic optimization, not a decision needing input — so
  no list-of-candidates normalization is implemented; a list response would
  need revisiting once the real shape is confirmed. The swap only applies
  when the returned price is strictly lower than the item's current
  `last_known_price`; an equal-or-higher price is treated the same as no
  equivalent.
- **Bonuses/promo-codes listing tool/shape**: also not named in the PRD or
  issue text beyond "apply available loyalty bonuses/promo codes." Assumed a
  tool named `silpo_get_available_bonuses` taking no arguments and returning
  a list of dicts shaped like `{"id": ...}` (a bare id string in the list is
  also accepted, same defensive normalization style as elsewhere in this
  codebase). An empty/absent list means nothing to apply, and
  `silpo_update_shopping_cart` is not called at all in that case — mirrors
  the Cart Writer's "no items to add, no call" pattern from issue #6.
- **`silpo_update_shopping_cart` request shape**: assumed to accept
  `{"bonus_ids": [...]}` and apply to the cart as a whole (not per-item),
  per the PRD's Promo Optimizer wording ("apply available loyalty
  bonuses/promo codes to the cart as a whole"). This is the ticket's least
  confirmed assumption alongside the promo-equivalent lookup tool name —
  revisit both first once `tools/list` is inspected live.
- **Pipeline placement**: `cli.py` calls `optimize_promos` on the
  Substitution Resolver's `.items` output, and its (possibly promo-swapped)
  items feed `write_cart`, per the PRD order Address Resolver -> Order
  Aggregator -> Substitution Resolver -> (optional) Promo Optimizer -> Cart
  Writer. Promo swap / bonus-applied report lines are printed after the
  substitution/unavailable lines, before the trim/added lines.
- **Flag shape**: `--optimize promos` is an `argparse` `choices=["promos"]`
  option (not a bare boolean flag), per the PRD's Promo Optimizer section
  and issue title exactly as written — `--optimize` with no value or an
  unrecognized value is rejected by argparse rather than silently ignored.
