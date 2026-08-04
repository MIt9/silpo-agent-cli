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
