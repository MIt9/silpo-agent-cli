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
