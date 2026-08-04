# Silpo MCP server — live schema notes

Status: **`silpo_agent/auth.py`'s own login flow now works end-to-end
live**, and `silpo-agent reorder` has been run against the real account
through its own binary (2026-08-04) — see "Live end-to-end run" below.
Everything under "Live-verified: address tools" / "Live-verified: all
remaining tools" was originally captured via a separately-configured MCP
client connection (not this project's own `auth.py`); that channel's
findings are unaffected by anything below.

## Live end-to-end run (2026-08-04, via `auth.py`'s own login)

`auth.py`'s browser login was previously hard-blocked by Cloudflare — root
cause was `redirect_uri` using the `127.0.0.1` IP literal instead of the
`localhost` hostname (fixed, see `mcp_auth_cloudflare_block.md` in the
assistant's memory for the full trail). After that fix, running
`uv run silpo-agent reorder --last 5 --threshold 0.5` against the real
account surfaced two more real bugs, now fixed:

1. **`call_tool_http()` never unwrapped the MCP `tools/call` response
   envelope.** The real transport wraps a tool's JSON output as a string
   inside `result.content[0].text`, not as already-parsed data. Every
   module was silently getting `{"content": [...]}` instead of the tool's
   actual response — invisible to unit tests, since they all mock `call()`
   itself, above this transport layer. Fixed with a small
   `_unwrap_tool_result()` helper in `auth.py`.
2. **`cli.py` never unwrapped `silpo_get_my_online_orders`'s response**,
   passing `{"success", "summary", "orders": [...], "meta": {...}}`
   straight to `derive_typical_items()`, which does `len(orders)` expecting
   a list — `len()` on a 4-key dict silently produced `"found 4"`
   regardless of real order count (the account has 326). Also added an
   explicit `limit` param to the call (the tool defaults to 10), since
   `--last` values above 10 would otherwise silently under-count.

With both fixed, a full `reorder` run now completes the real pipeline:
address confirmed with a readable label, cart context resolved (including
real `validations` — this account's actual cart has a stale timeslot and
two out-of-stock items), the non-empty-cart guard correctly triggered and
aborted cleanly on decline (cart left unchanged), typical items derived
from real order history, and Substitution Resolver ran — reporting all
checked items "unavailable". That last part matches issue #18's own
documented weakest assumption (using `TypicalItem.product_id`, a raw UUID,
as the free-text query to `silpo_find_products_batch`) rather than
indicating a new bug — worth a dedicated follow-up ticket if the
Substitution Resolver's real-world hit rate matters (e.g. searching by
`TypicalItem.name` first, only falling back to product_id, is already
partially done per #18 — but a raw UUID search apparently still returns
nothing useful when name is absent, which was the assumed risk).

## Live-verified: address tools (2026-08-04)

Confirmed by direct live calls. **`silpo_agent/address_resolver.py`'s
assumptions are wrong in four ways** — tracked as a follow-up ticket, not
fixed in this note:

- **`silpo_get_my_delivery_addresses`** takes no args. Real response:
  ```json
  {"success": true, "summary": "Found 9 delivery addresses", "addresses": [
    {"id": "uuid", "tag": null, "city": "...", "street": "...",
     "building": "...", "apartment": "...|null", "floor": "...|null",
     "entrance": "...|null", "latitude": 49.x, "longitude": 28.x,
     "comment": "...|null"}
  ]}
  ```
  Top-level is `{"success", "summary", "addresses"}`, not a bare list.
  **No `"is_default"` field exists anywhere** — `address_resolver.py`'s
  `next((a for a in addresses if a.get("is_default")), addresses[0])` always
  falls through to `addresses[0]`, i.e. "MCP-marked default" (the
  CONTEXT.md/PRD assumption) doesn't exist server-side; it's really just
  "whatever order the API returns them in." **No `"address"` or `"label"`
  field either** — `_to_resolved()`'s label fallback
  (`address.get("address") or address.get("label") or address.get("id")`)
  always falls through to the raw UUID `id`, so every confirmation prompt
  currently displays a UUID instead of a readable address (e.g. "Deliver to
  9930af7e-07be-4f3a-898a-3ed7435ec655?"). Needs a real label built from
  `city`/`street`/`building`/`apartment`.
- **`silpo_find_address`** takes `{"address": "<free text>"}` — **not**
  `{"query": ...}` as `address_resolver.py` currently sends
  (`client.call("silpo_find_address", {"query": query})`). Real response:
  ```json
  {"success": true, "summary": "Found 1 addresses", "addresses": [
    {"address": "Вінниця, Варшавська вулиця, 27", "city": "...",
     "street": "...", "houseNumber": "...", "district": "...",
     "latitude": 49.x, "longitude": 28.x}
  ]}
  ```
  This is a geocode result, not a saved-address record — **no `"id}"`
  field**, so `_enter_new_address`'s `resolved.id` is always `None` for a
  newly-entered address. Field is `houseNumber`, not `building` (differs
  from the saved-address shape above).
- **`silpo_get_available_delivery_types`** takes `{"latitude": ..., "longitude": ...}`
  — **not** `{"address_id": ...}` as `address_resolver.py` currently sends.
  Since a freshly-geocoded address also has no `id`, this call is broken two
  ways at once: wrong param name, and the value it would send is always
  `None`. This means the branch/delivery-context resolution step — which the
  PRD calls out as the reason address resolution runs before product search
  — is not actually happening correctly today for the new-address path.

## Live-verified: all remaining tools (2026-08-04)

Full sweep of the Silpo MCP server's 39 `silpo_*` tools, via the same
authenticated connection as the address-tools sweep above (the MCP server
only responds to an authorized user — every call below required a real,
logged-in Silpo account). Read-only tools were called directly and their
real response shapes captured below. **Mutating tools were NOT called**
(they would have changed the real, connected Silpo account's cart,
certificates, or favorites) — their schemas below come from the tool
definitions themselves (still ground truth, just unexercised).

This sweep invalidates several assumptions across almost every module, not
just Address Resolver. Summarized first, details below:

- **Cart Writer's core assumption is wrong**: `silpo_get_my_shopping_cart`
  does NOT return cart contents — it returns only a `shoppingCartId`. Actual
  cart contents (items, branch, delivery type, timeslot, totals, validation
  errors) come from a second call, `silpo_get_shopping_cart_by_id`.
- **Order Aggregator's field names are wrong**: an order's line items are
  under `"products"`, not `"items"`; each item's id field is `"id"`, not
  `"product_id"`. Also confirms newest-first ordering (already assumed
  correctly).
- **`silpo_add_or_update_cart_products`'s payload is far more specific**
  than assumed: each product needs `productId` + `companyId` + `branchId` +
  `quantity`, plus a `shoppingCartId` — not just `product_id`/`quantity`.
- **Substitution Resolver's `silpo_check_availability` tool doesn't
  exist.** Availability is a field (`stock`, `available`) on product records
  returned by `silpo_get_products` / `silpo_find_products_batch` /
  `silpo_get_similar_products` / `silpo_get_product_details` — there's no
  standalone availability-check call.
- **Promo Optimizer's two invented tools don't exist.** There's no
  per-product "cheaper promo equivalent" lookup and no separate
  "apply bonuses" call. Real promo/bonus handling is structurally different
  — see the Promo/loyalty tools section below.

### Cart tools

- **`silpo_get_my_shopping_cart`** — no args. Real response:
  `{"success": true, "shoppingCartId": "<uuid>"}`. Nothing else. Must be
  followed by `silpo_get_shopping_cart_by_id` for actual contents.
- **`silpo_get_shopping_cart_by_id({"shoppingCartId": "..."})`** — real
  response (trimmed):
  ```json
  {"success": true, "cart": {
    "id": "uuid", "deliveryType": "DeliveryHome",
    "timeslot": {"start": "...", "end": "..."},
    "address": {"addressType": "flat", "latitude": "49.x", "longitude": "28.x",
      "city": "...", "street": "...", "house": "...", "flat": "...", "...": "..."},
    "shipments": [{"id": "uuid", "companyId": "uuid", "branchId": "uuid",
      "products": [{"productId": "uuid", "companyId": "uuid", "branchId": "uuid",
        "slug": "...", "name": "...", "quantity": 2, "price": 49.9,
        "oldPrice": 69.9, "subTotal": 139.8, "subDiscount": 40, "total": 99.8,
        "stock": 0, "weighted": false, "addToBasketStep": 1, "comment": null}]}],
    "certificates": [], "isAdultConfirmed": false, "promoCode": null,
    "calculation": {"total": 166.7, "totalAfterDiscounts": 166.7,
      "certificatesTotal": 0, "subTotal": 228.29, "subDiscount": 61.59,
      "productsTotal": 166.7,
      "delivery": {"total": 0, "totalWeight": 2.67, "deliveryExpressByPromise": {...}},
      "payment": {"availableTypes": [...]},
      "validations": [
        {"level": "error", "type": "timeslot", "message": "timeslot.not_found", "context": []},
        {"level": "error", "type": "product", "message": "product.offer.stock.max",
         "context": {"productId": "uuid", "stock": 0}}
      ]}},
   "loyalty": {"bonusAvailable": 24.27, "bonusTotal": 24.27, "bonusRequested": null, "isEnabled": true}}
  ```
  Key points for Cart Writer: use `cart.shipments[0].branchId` +
  `cart.deliveryType` + `cart.timeslot` as the search context (this is the
  authoritative source the PRD/Address Resolver ticket was trying to
  approximate). `cart.calculation.validations[]` reports real problems
  (out-of-stock items, stale timeslots) — worth surfacing to the user rather
  than only trusting a bare item list. `cart.calculation.totalAfterDiscounts`
  is what the user actually pays; `total` is pre-discount.
- **`silpo_add_or_update_cart_products`** (mutating, not called) — real
  request: `{"shoppingCartId": "...", "products": [{"productId": "...",
  "companyId": "...", "branchId": "...", "quantity": N, "addQuantity": bool,
  "comment": "..."}]}`. `companyId`/`branchId` must come from a product
  search result (or the existing cart/order), not invented — every product
  record everywhere in this API carries its own `companyId`/`branchId`.
  Quantity for weighted goods must be a multiple of the product's
  `addToBasketStep` (e.g. 0.35 step seen on cucumbers). Tool description
  also says: always ignore/skip plastic bags in cart writes, and always
  verify via `silpo_get_shopping_cart_by_id` afterward.
- **`silpo_remove_cart_products`** (mutating, not called) — `{"shoppingCartId",
  "products": [{"productId": "..."}]}`.
- **`silpo_clear_shopping_cart`** (mutating, destructive, not called) —
  `{"shoppingCartId"}` only.
- **`silpo_update_shopping_cart`** (mutating, not called) — the real
  "apply bonuses/promo code" mechanism: `bonusRequested` (number of
  Балабонуси to apply, or `null` to remove) and `promoCode` (string or
  `null`) are just two more fields on this single cart-update call, alongside
  `deliveryType`/`timeslot`/`address`/`shipments` (which per the tool's own
  description must be copied from `silpo_get_shopping_cart_by_id`'s
  response, not constructed). There is no separate "apply bonuses" tool —
  Promo Optimizer's assumed `silpo_get_available_bonuses` +
  `silpo_update_shopping_cart({"bonus_ids": [...]})` design doesn't match
  reality on either count.
- **`silpo_add_or_update_certificates`** (mutating, not called) —
  `{"shoppingCartId", "certificatesToAdd": [{"barcode", "pincode"}],
  "certificatesToRemove": [{"barcode", "pincode"}]}`, max 10 each.

### Order history tools

- **`silpo_get_my_online_orders({"limit", "offset"})`** — real response:
  `{"success": true, "summary": "Found N orders (total: T)", "orders": [
  {"orderId": "uuid", "number": "...", "status": "received", "createdAt":
  "ISO", "amount": 2077.38, "discount": 486.88, "delivery": {"type":
  "DeliveryHome", "timeSlot": {"from", "to"}, "deliveredAt": "ISO"},
  "address": {"city", "street", "building", "apartment"}, "products": [
  {"id": "uuid", "name": "...", "price": 66.9, "quantity": 1, "subtotal":
  66.9, "removed": false, "image": "...", "companyId": "uuid", "branchId":
  "uuid"}]}], "meta": {"limit", "offset", "total"}}`.
  **Confirms newest-first ordering** (already assumed correctly by
  `order_aggregator.py`) — three fetched orders came back
  2026-07-20 → 2026-07-03 → 2026-06-24, descending. **Wrong field names in
  current code**: the line-item list is `"products"`, not `"items"`; each
  item's id field is `"id"`, not `"product_id"`. Items carry a `"removed"`
  boolean — a product can appear with `"removed": true` (e.g. a tomato
  removed from the order after ordering but before delivery, still listed
  with its original price/subtotal) — `derive_typical_items` should
  probably skip `removed: true` line items when counting frequency, which it
  currently can't do since it doesn't know this field exists. No explicit
  "paid"/"confirmed" field on the order — `status: "received"` is the closest
  signal; the PRD's assumption that this endpoint already filters to
  confirmed/paid orders is unconfirmed either way. `quantity` can be
  fractional for weighted goods (e.g. `1.232` kg of nectarines).
- **`silpo_get_my_offline_orders({"branchId", "deliveryType",
  "timeslotStart", "timeslotEnd", "dateStart", "dateEnd", "limit" (max 10),
  "offset"})`** — in-store purchase history, separate from online orders and
  explicitly out of this project's v1 scope, but now schema-documented for
  completeness: each order has `filId`, `filialName`, `cityName`,
  `createdAt`, `sumReg`, `accruedBalaBonusesSum`, `sumDiscount`,
  `receiptUrl`, `chequeMagicName`, `rewards[]`, and `products[]` where each
  product has `lagerId`, `name`, `unit`, `quantity`, `price`, `image`, and a
  nested `catalogProduct` (null if the product can no longer be matched to
  the catalog) with `id`/`slug`/`price`/`stock`/`available`/`companyId`/
  `branchId` — `catalogProduct !== null` means it's reorderable via
  `silpo_add_or_update_cart_products`.

### Product search / replacement tools

- **`silpo_get_replacements({"branchId", "companyId", "productIds": [...],
  "deliveryType"})`** — confirmed as a **batch** call (multiple product ids
  at once), requiring `companyId`/`branchId`/`deliveryType` context — not
  the single-`product_id` shape `substitution_resolver.py` currently
  assumes. Real response shape: `{"success": true, "summary": "Found
  replacements for N products", "items": [...]}`. Tested live against two
  genuinely out-of-stock cart items and got `"Found replacements for 0
  products"` (empty `items`) — so the populated-item shape is still
  unconfirmed; worth retesting against a product with known replacements
  before relying on field names inside `items[]`. See "Assumptions made in
  issue #18" below for the shape `substitution_resolver.py` currently
  assumes for a populated entry.
- **Product record shape** (same shape returned by `silpo_get_products`,
  `silpo_find_products_batch`, and `silpo_get_similar_products` — this is
  the shape Substitution Resolver and Promo Optimizer should both consume):
  `{"id", "name", "slug", "price", "oldPrice", "stock", "available",
  "image", "weighted", "step", "specialPrices", "companyId", "branchId",
  "externalProductId"}`. `available` (bool) + `stock` (int) together are the
  real availability signal — confirms no standalone
  `silpo_check_availability` tool exists; this is what Substitution
  Resolver's availability check should read instead.
- **`silpo_find_products_batch({"branchId", "deliveryType",
  "timeslotStart", "timeslotEnd", "products": [...], "limit"})`** — real
  response: `{"success": true, "summary": "...", "queries": [{"query": "...",
  "totalFound": N, "products": [...]}], "meta": {"totalQueries",
  "totalProducts"}}` — grouped per input query string, not a flat list.
- **`silpo_get_products({"branchId", "deliveryType", "timeslotStart",
  "timeslotEnd", "category"|"mustHavePromotion"|"promotionCode"|"set", ...})`**
  — requires at least one of the four filters; `timeslotStart`/`timeslotEnd`
  are hard-required by the schema (a call without them errors immediately —
  confirmed live). This is the closest thing to a "promo equivalent"
  browser: `mustHavePromotion: true` (optionally + `promotionCode` from
  `silpo_get_promotions`) returns promotional products, but there is no
  per-product "find the promo version of THIS product" call — a real Promo
  Optimizer would need to search by category/name and match, not do a
  single lookup.
- **`silpo_get_product_details({"branchId", "slug", "deliveryType",
  "timeslotStart", "timeslotEnd"})`** — real response: `{"success": true,
  "product": {"id", "name", "slug", "price", "oldPrice", "stock",
  "available", "weighted", "step", "ratio", "url", "images": [...],
  "attributes": {"<label>": "<value>", ...}, "companyId", "branchId"}}`.
  `slug` must come from a prior search result, never guessed from a name.
- **`silpo_get_similar_products({"branchId", "slug", "deliveryType",
  "limit", "offset"})`** — same product-record shape as above, wrapped in
  `{"success", "summary", "products": [...], "meta": {"total"}}`. A
  plausible real replacement-candidate source for Substitution Resolver if
  `silpo_get_replacements` turns out thin in practice.

### Promo / loyalty tools

None of these match the tool names `promo_optimizer.py` invented
(`silpo_get_promo_equivalent`, `silpo_get_available_bonuses`) — real promo
handling is spread across several tools with a different shape:

- **`silpo_get_promotions({"branchId", "deliveryType", "timeslotStart",
  "timeslotEnd"})`** — branch-wide promotion *categories*, not per-product:
  `{"success", "summary", "promotions": [{"code", "title", "productCount",
  "url"}]}`. `code` feeds `silpo_get_products(promotionCode=...)`.
- **`silpo_get_my_promos()`** — personal "select which frequency-promos to
  activate" offers (not directly related to a specific cart item):
  `{"success", "summary", "promos": [{"promoId", "selected", "beginDate",
  "endDate", "description", "rewardText", "rewardValue", "limitText",
  "warningText", "addressListText", "image"}], "meta": {"total", "minSelect",
  "maxSelect"}}`. No tool in this MCP server's 39 tools writes/activates a
  promo selection — this may be app-only, or an omission; unconfirmed.
- **`silpo_get_promo_codes()`** — user's own promo codes (empty for this
  account): `{"success", "summary", "promoCodes": [], "meta": {"total"}}`.
- **`silpo_get_my_coupons()`** — `{"success", "summary", "coupons": [{"id",
  "active", "useWay", "beginDate", "endDate", "description", "limitText",
  "warningText", "image"}]}`. `silpo_get_coupon_details({"businessCouponId"})`
  takes `coupons[].id`.
- **`silpo_get_my_certificates({"limit", "offset"})`** — `{"success",
  "summary", "certificates": [{"id", "createdAt", "totalPrice", "barcode",
  "pincode", "expireDate", "title", "image"}]}`.
- **`silpo_get_loyalty_info()`** — `{"success", "loyalty": {"card":
  {"barcode", "typeName", "memberId"}, "balance": {"total", "currency",
  "accounts": [{"type", "amount"}]}}}`. `balance.total` is the same value as
  `bonusAvailable` seen on the cart response.

None of these are wired into `promo_optimizer.py` today — issue #7's module
needs a real redesign, not a field-name patch, once this becomes a live
follow-up ticket.

### Profile / account tools (not used by the `reorder` pipeline, documented for completeness)

- **`silpo_get_my_profile()`** — `{"success", "profile": {"id", "firstName",
  "lastName", "middleName", "phone", "email", "birthday", "gender",
  "status"}}`.
- **`silpo_get_my_family()`** — `{"success", "summary", "name", "members":
  [{"profileId", "name", "phone", "image", "profileCreatedAt", "itsMe"}],
  "children": [{"id", "name", "slug", "dateOfBirth"}], "pets": [{"id",
  "name", "slug"}]}`.
- **`silpo_get_my_favorites({"branchId", "deliveryType", "timeslotStart",
  "limit", "offset"})`** (not called live — needs the same branch/timeslot
  context as product search) — per its tool description, returns products
  in the same shape as `silpo_get_products`.
- **`silpo_add_or_update_favorite_products`** (mutating, not called) —
  `{"actions": [{"productId", "externalProductId", "toDelete"}]}`, max 5.
- **`silpo_get_my_food_restrictions()`** — `{"success", "summary",
  "restrictions": []}` (empty for this account).
- **`silpo_get_my_premium_subscription()`** — when inactive: `{"success",
  "summary", "webLink", "mobileLink"}`; per the tool description, an active
  subscription instead returns share links (`shareWebLink`/`shareMobileLink`)
  — not verified live (this account has no active subscription).

### Location / branch / delivery tools

- **`silpo_list_branches({"hasPickup", "hasNP", "limit", "offset"})`** —
  response too large to fully inspect in one call (121K+ chars for the
  default page), but its top-level schema is confirmed: `{"success",
  "summary", "branches": [...], "meta": {"limit", "offset", "total"}}`.
- **`silpo_get_time_slots({"branchId", "deliveryTypes", "start", "end",
  "limit"})`** — `{"success", "summary", "slots": [{"start", "end",
  "available", "deliveryType", "deliveryCost", "deliveryCostMap": [{"cost",
  "fromOrderCost"}], "minOrderCost", "maxWeight", "constraints": {...},
  "fast"}], "meta": {"total"}}`. All times UTC. Live-tested: this account's
  current cart timeslot (`2026-08-04T07:00–08:30`) came back
  `"available": false` alongside two other slots — matches the
  `"timeslot.not_found"` validation error seen on
  `silpo_get_shopping_cart_by_id` above; a real Cart Writer should probably
  check this before adding items, not just before/after.
- **`silpo_get_categories_tree`** / **`silpo_get_categories`** /
  **`silpo_get_category`** / **`silpo_get_popular_categories`** /
  **`silpo_get_product_sets`** — category/browse tools, not currently used
  by any module in this project. `silpo_get_popular_categories` and
  `silpo_get_product_sets` response shapes confirmed live:
  `{"success", "summary", "categories": [{"id", "slug", "title", "url"}]}`
  and `{"success", "summary", "sets": [{"slug", "title", "description",
  "link"}]}` respectively. `silpo_get_categories_tree`'s top-level schema is
  `{"success", "summary", "tree": [...]}` (full tree too large to inspect in
  one call).
- **`silpo_find_nova_poshta_settlements`** / **`silpo_find_nova_poshta_offices`**
  — not called live; out of scope for this project (v1 only handles
  `DeliveryHome`/saved-address delivery, no Nova Poshta flow). Param shapes
  only, from the tool definitions.

## Original status note (superseded above for address tools)

Status as of the original foundations ticket: **auth endpoints verified
live, `tools/list` schema NOT verified** — see "What's unverified" below.

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

**Superseded by issue #18** — the `silpo_check_availability` tool and the
per-item `silpo_get_replacements` shape described below were both invented
and did not survive contact with the live-verified schema; see "Assumptions
made in issue #18" further down for what `substitution_resolver.py` actually
does now. Left here for history.

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

## Assumptions made in issue #18 (fix Substitution Resolver against live-verified MCP schema)

Issue #18 replaced the invented `silpo_check_availability` tool and the
per-item `silpo_get_replacements` call in `silpo_agent/substitution_resolver.py`
with the two real, live-verified tools documented above
("Product search / replacement tools"). Two things in the new
implementation are still assumptions, not confirmed live:

- **Availability-check query term**: `silpo_find_products_batch`'s
  `products` field is a list of free-text search queries. Issue #19 (merged
  into `main` mid-#18) added a `name` field to `TypicalItem`, so
  `_check_availability`/`_search_query` in `substitution_resolver.py` now
  search on `item.name` when present — a real product name is a materially
  better free-text query than a raw id/UUID — falling back to
  `item.product_id` only for items that don't carry a name (substitution and
  promo results, which don't currently carry one through; see `_to_item` /
  `promo_optimizer.py`). Still unconfirmed against a live call whether the
  real search endpoint matches a `name`-as-written query back to the exact
  same product (vs. a near-duplicate/variant) — `queries[].query` is matched
  back to the original item(s) that used that exact query string, and the
  *top* result for that query is trusted as the match. If that assumption
  turns out wrong, `silpo_get_product_details({"branchId", "slug", ...})`
  would be a more precise fallback, but that needs a `slug`, which no
  `TypicalItem` currently carries.
- **Populated `silpo_get_replacements` item shape (still genuinely
  unconfirmed)**: as noted above, a live call against real out-of-stock
  items returned an empty `items` array, so the shape of a populated entry
  has never been observed. `_fetch_replacements` assumes each entry looks
  like `{"productId": "<original id>", "replacements": [<product record>,
  ...]}`, where each replacement candidate uses the general product-record
  shape (`"id"`, `"price"`, `"stock"`, `"available"`, ...) documented above
  — a guess grounded in the shape used consistently elsewhere in this MCP
  server, not a verified response. `_fetch_replacements` still normalizes a
  single dict to a one-item list for `"replacements"`, keeping the
  defensive pattern used throughout this codebase. Revisit this first once
  a live call against a product with real replacement candidates is
  possible.
- **Context source for both calls**: both `_check_availability` and
  `_fetch_replacements` take `branchId`/`companyId`/`deliveryType`/timeslot
  from the `CartContext` resolved by #17 (the *current* session's cart
  context), not from `TypicalItem.company_id`/`.branch_id` (the *historical*
  order's context, added in #16). Chosen because availability/replacements
  must reflect where the user is ordering from right now, not where a past
  order happened to ship from; `TypicalItem.company_id`/`.branch_id` are
  left unused by this resolver as a result.
- **Batching**: both tool calls batch across all items in a single
  `resolve_substitutions` run (one `silpo_find_products_batch` call for all
  typical items, one `silpo_get_replacements` call for all items found
  unavailable) rather than one call per item, per the ticket's real batch
  shape. The per-item substitution *decision* logic (0/1/>1 candidates,
  Substitution Memory reuse) is unchanged from issue #5.
- **No-cart-context guard**: `resolve_cart_context` returns an all-`None`
  `CartContext` when the user has no shopping cart yet (a real, normal state
  — first-ever run, or a cleared cart), not an error. `resolve_substitutions`
  guards on `cart_context.branch_id`/`.company_id` being `None` and skips
  both MCP calls entirely in that case (reporting every item unavailable)
  rather than sending `None` fields to the real API. Found in code review of
  the initial #18 PR, before it reached `main`.
- **Candidate id key fallback**: since the populated `silpo_get_replacements`
  item shape is unconfirmed (see above), candidate id lookups try `"id"`
  first (the general product-record shape) and fall back to `"productId"`
  (the key used by confirmed cart/order product shapes elsewhere in this
  codebase) rather than hard-failing with a `KeyError` if the real shape
  turns out to differ.

## Scope decision made in issue #20 (redesign Promo Optimizer against live-verified MCP schema)

Issue #20 replaced the two invented tools in `silpo_agent/promo_optimizer.py`
(`silpo_get_promo_equivalent`, `silpo_get_available_bonuses` — see
"Assumptions made in issue #7" above) with the real Promo/loyalty tools
documented above. **Scope was narrowed to bonus application only; the
promo-swap half ("swap a Typical Item for a cheaper promo equivalent") was
dropped entirely.** The issue explicitly permitted this narrowing if the
swap heuristic proved too unreliable to be worth shipping, which is the
judgment call made here:

- **Why the swap was dropped**: the only real tool that browses promo
  products is `silpo_get_products({"mustHavePromotion": true,
  "promotionCode": "<code from silpo_get_promotions>", ...})`, and per its
  documented schema it only filters by `category`/`mustHavePromotion`/
  `promotionCode`/`set` — no free-text search parameter is documented
  anywhere in the live-verified sweep. Without a text-search filter, "find
  the promo equivalent of Typical Item X" would mean either (a) guessing an
  unconfirmed extra parameter on `silpo_get_products`, or (b) paging through
  entire promo categories and fuzzy-matching product names against the
  Typical Item's `name` — genuinely unreliable (false-positive swaps to a
  wrong, merely-similarly-named product are a real risk with money on the
  line) and not something this ticket could validate against a live call.
  This is exactly the "heuristic proves too unreliable" case the issue
  anticipated; rather than ship a name-matching guess against an unverified
  parameter, the swap feature is cut. `TypicalItem` still carries `name` for
  a future ticket that wants to revisit this once `silpo_get_products`'s
  full parameter list is confirmed live (e.g. an actual search/query field
  might exist but wasn't exercised in the sweep).
- **Why bonus application was kept**: it needs no matching heuristic and no
  new lookup call. `silpo_get_shopping_cart_by_id`'s response — already
  fetched once per `reorder` run by the Cart Context Resolver (#17) — carries
  `loyalty.bonusAvailable` alongside the cart itself. `CartContext` (in
  `cart_context.py`) was extended with `bonus_available` (from
  `response["loyalty"]["bonusAvailable"]`) plus the raw `timeslot`/
  `address`/`shipments` objects `silpo_update_shopping_cart` requires
  verbatim per its own tool description (not reconstructed field-by-field —
  same precedent as issue #19 extending `CartContext` for `.products`).
  `optimize_promos(client, items, cart_context)` calls
  `silpo_update_shopping_cart` with `bonusRequested=cart_context.bonus_available`
  only when that value is truthy AND `shoppingCartId`/`deliveryType`/
  `timeslot`/`address`/`shipments` are all present on `cart_context` — the
  last four are required verbatim by the call, so a resolved-but-incomplete
  context (e.g. no shipments recorded yet, a legitimate `CartContext` state)
  skips the call rather than send `None` into a required field, same guard
  style as Substitution Resolver's (#18) no-cart-context skip. No bonus
  (`None`/`0`) or an unresolved cart context (first-ever run) also makes
  zero calls, mirroring the "nothing to do, no call" pattern used by Cart
  Writer (#19). The call's response is checked too (`response.get("success")`,
  the envelope every real tool response uses per this doc) before reporting
  `bonus_applied` — a mutating call's success is never assumed silently, per
  code review on the #20 PR.
- **`promoCode` intentionally left `null`**: `silpo_get_promo_codes()`
  returned an empty list for the live test account, so there is no verified
  case to apply a promo code against — the issue explicitly said not to
  over-build for an unverified case. If a later ticket confirms a non-empty
  `silpo_get_promo_codes()` response for some account, wiring `promoCode`
  onto this same `silpo_update_shopping_cart` call is a small, additive
  change to `optimize_promos`, not a redesign.
- **`PromoResult` shape change**: `.swaps` and `.bonuses_applied` (a list of
  bonus/promo-code ids) are gone; `PromoResult` now carries `.items`
  (pass-through, unchanged — nothing rewrites the cart line items anymore)
  and `.bonus_applied` (the numeric amount requested, or `None`).
  `cli.py`'s report line changed from "Promo swap: X -> Y" /
  "Applied bonuses/promo codes: ..." to "Applied N.NN bonus points to cart".
- **Pipeline placement unchanged**: `cli.py` still calls `optimize_promos`
  between Substitution Resolver and Cart Writer, only when `--optimize
  promos` is passed — a plain `reorder` makes zero calls to
  `silpo_update_shopping_cart` (verified in `test_cli.py`'s flag-off test,
  which now also asserts no such call even when a bonus balance IS
  available on the resolved cart, since the flag alone must gate it).
