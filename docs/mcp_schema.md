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
  is what the user actually pays; `total` is pre-discount. (Issue #27:
  `CartContext.total_after_discounts` now carries this through -- no new
  schema, `cart_context.py` previously just didn't read this field.)
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
  top-level schema: `{"success", "summary", "branches": [...], "meta":
  {"limit", "offset", "total"}}`. **Per-branch shape, live-verified
  2026-08-05 (issue #38):** `{"branchId", "companyId", "externalId", "city",
  "address", "latitude", "longitude", "hasPickup", "open"}` — `latitude`/
  `longitude` come back as strings (e.g. `"50.5202200000000000"`), not
  numbers. `hasPickup=true` returned 311 branches total (default page
  limit 50); `hasNP=true` returned exactly **1** branch nationwide
  (`branchId: "1ee7fab3-7713-6a0c-b802-8d149aac137a"`, Київ) — so resolving
  a NovaPoshta shipment's branch is a lookup, not a user pick. `open: false`
  branches were present in the `hasPickup=true` sample (e.g. a closed
  Kyiv branch) — `delivery_settings.py`'s `_pick_self_pickup_branch` filters
  these out before listing pickup options (review finding on PR #49, fixed
  2026-08-05), so a closed branch is never offered as a pick.
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
- **`silpo_find_nova_poshta_settlements({"title"})`** — live-verified
  2026-08-05 (issue #38), searched `"Київ"`: `{"success": true, "summary":
  "Found 3 settlements", "settlements": [{"id", "title", "area",
  "region"}]}`, e.g. `{"id": "4976878b-ccaf-4ecf-8f74-96ae0d0c6e10",
  "title": "Київ", "area": "Київська", "region": ""}`.
- **`silpo_find_nova_poshta_offices({"settlementId", "title"})`** —
  live-verified 2026-08-05 against the settlement above: `{"success": true,
  "summary": "Found N offices", "offices": [{"id", "title", "address",
  "type", "number", "status", "latitude", "longitude"}], "meta": {"total"}}`.
  `type` is `"office"` or `"parcelLocker"`; `latitude`/`longitude` here are
  **numbers** (unlike `silpo_list_branches`, where they're strings).
  Matches the tool definitions' assumed param/field names exactly — no
  correction needed here (see "Live-verified: SelfPickup / Nova Poshta
  address construction" below for the branch-field correction that WAS
  needed).

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

## Assumptions made in issue #29 (Cart Context Resolver's no-shipments address fallback)

`cart_context.py`'s `resolve_cart_context` previously left `branch_id`/
`company_id`/`delivery_type` as `None` whenever `cart.shipments` was empty
(fresh account, or a cleared cart). Issue #29 added a fallback for that case:
run `address_resolver.resolve_address` to get a confirmed address, then
derive real branch/delivery context from `silpo_get_available_delivery_types`
(the same call `resolve_address` already makes for that address's
coordinates — its result is now kept on `ResolvedAddress.delivery_types`
instead of discarded, so this fallback reuses it rather than issuing a
second, duplicate call with the same lat/lon).

- **`silpo_get_available_delivery_types` response shape is still
  unconfirmed live** (see "Assumptions made in issue #4" above — only the
  request shape, `{"latitude", "longitude"}`, has been live-verified; the
  response was never inspected since no delivery-type selection UX existed
  before now). `cart_context._branch_context_from_delivery_types` therefore
  tries a few plausible shapes defensively rather than assuming one: a
  top-level `branchId`/`companyId`/`deliveryType`, or (falling back) the
  first entry — preferring one with `available: True` if that field exists —
  of a `deliveryTypes` list, reading `branchId`/`companyId`/`type`/
  `deliveryType` off it. If the real response turns out to be shaped
  differently, only this one helper needs revisiting. Genuinely unresolvable
  input (missing/malformed) yields `(None, None, None)`, same as before this
  ticket — no crash, no fabricated context.
- **Reuse over duplication**: `resolve_address` now returns its
  `silpo_get_available_delivery_types` call's raw response on
  `ResolvedAddress.delivery_types` (`None` when coordinates were missing and
  the call was skipped). `resolve_cart_context` reads this field directly
  when it has a `resolved_address`, making zero extra MCP calls of its own
  for that path.
- **No-double-prompt handling**: `reorder`'s pipeline (`cli.py`'s
  `_run_reorder`) already resolves an address via `resolve_address` before
  calling `resolve_cart_context`. It now passes that address in as
  `resolve_cart_context(client, resolved_address=address, ...)`, so the
  fallback (which only activates when `cart.shipments` is empty) reuses it
  instead of calling `resolve_address` a second time — verified in
  `test_cli.py`'s `test_reorder_no_shipments_cart_reuses_address_without_second_prompt`,
  which counts address-confirmation prompts and asserts exactly one.
- **Self-resolving path for future callers**: commands other than `reorder`
  (`cart`, `cart promos`, `cart edit`, `deals`, `favorites-deals` — none
  built yet) will call `resolve_cart_context` directly with no address of
  their own. For that case, `resolve_cart_context` now accepts optional
  `log_store`/`input_fn`/`print_fn` and, when `resolved_address` isn't
  given, runs `resolve_address`'s own interactive confirm/pick/new-address
  flow itself (defaulting `log_store` to a real `ReorderLogStore()` so the
  confirmed address is still logged for audit, matching `resolve_address`'s
  existing behavior everywhere else it's called). Only `branch_id`/
  `company_id`/`delivery_type` are filled in by this fallback — `timeslot_start`/
  `timeslot_end` are left `None` even after a successful fallback, since
  `silpo_get_available_delivery_types` carries no timeslot data; a real
  timeslot needs a separate `silpo_get_time_slots` call, out of scope here.
- **Update (issue #37, same day)**: the "still unconfirmed live" note above
  is now out of date -- issue #37's `delivery` command live-verified
  `silpo_get_available_delivery_types`'s real response shape (see the next
  section). Real top-level key is `"options"`, not `"deliveryTypes"`, and
  there is no top-level `branchId`/`companyId` -- so
  `_branch_context_from_delivery_types`'s defensive multi-shape guess above
  doesn't match the confirmed shape (`{"options": [{"deliveryType",
  "branchId", "description"}]}`) on either branch it tries. Left as-is here
  since fixing another ticket's already-merged code is out of scope for
  #37; flagging it for a follow-up on #29's own module.

## Live-verified: delivery-settings tools (2026-08-05, issue #37)

Direct live calls against the same authenticated connection as the earlier
sweeps, made while building the `delivery` command
(`silpo_agent/delivery_settings.py`).

- **`silpo_get_available_delivery_types`'s response shape** was unconfirmed
  before this ticket (only the request shape had been live-verified). Real
  response: `{"success": true, "summary": "Found 3 delivery options for "
  <lat>, <lon>"", "options": [{"deliveryType": "DeliveryHome", "branchId":
  "<uuid>", "description": "Regular delivery (groceries, fresh products)"},
  {"deliveryType": "NovaPoshta", "branchId": null, "description": "..."},
  {"deliveryType": "SelfPickup", "branchId": null, "description": "..."}]}`.
  Top-level key is **`"options"`**, not `"deliveryTypes"` as
  `prd_delivery_context_coupons.md` assumed. `branchId` is populated only
  for types resolvable directly from coordinates (`DeliveryHome`/
  `WideAssortDelivery`/`B2B`, per the tool's own description); `NovaPoshta`/
  `SelfPickup` come back with `branchId: null` and need their own follow-up
  lookups (`silpo_list_branches`, `silpo_find_nova_poshta_*`) -- out of
  scope here, left to issue #38.
- **`silpo_update_shopping_cart`'s own tool description** (read directly
  from the live tool definition, not guessed) spells out per-delivery-type
  address construction: for `DeliveryExpressByPromise`, copy
  address/shipments/timeslot from the current cart as-is and only change
  `deliveryType`. For `NovaPoshta`/`SelfPickup`, build a fresh address
  object from settlement/office/branch data (differently shaped for each).
  **For every other delivery type (including `DeliveryHome`), the address
  object "MUST be passed exactly as received from
  `silpo_get_shopping_cart_by_id` ... Do NOT construct the address
  manually -- always copy it from the cart response. The shipments array
  must also come from the cart response."** This directly confirms the
  precedent `promo_optimizer.py` (issue #20) already established --
  `CartContext.address`/`.shipments` are meant to be copied through, not
  reconstructed field-by-field.
- **`silpo_get_time_slots`**: confirmed the request only needs `branchId`
  (everything else -- `deliveryTypes`/`start`/`end`/`limit` -- is optional).
  `delivery_settings.py` passes `branchId` + `deliveryTypes: ["DeliveryHome"]`
  only, letting the server default the time window, and filters the
  response client-side to `available: true` slots before offering choices
  (matching the tool's own "Only pick slots where available=true" guidance).

## Assumptions made in issue #37 (Delivery Settings / `delivery` command)

- **Address object for a newly-picked `DeliveryHome` address is NOT fully
  reconstructed.** `address_resolver.py`'s `ResolvedAddress` (reused as-is,
  per this ticket's instructions) only carries `id`/`label`/`latitude`/
  `longitude` -- no `street`/`house`/`flat`/`floor`/`entrance`/etc. Given
  the live tool description above explicitly says not to hand-construct
  this address, `delivery_settings.py` takes `CartContext.address` (the
  existing cart's address object, in the exact shape
  `silpo_get_shopping_cart_by_id` returns) as a template and overrides only
  `latitude`/`longitude` (as strings, matching the live-observed shape --
  `cart.address.latitude`/`.longitude` are strings even though the saved
  -address record's own `latitude`/`longitude` are floats) with the newly
  resolved address's coordinates. Every other field (`city`/`street`/
  `house`/`flat`/`floor`/`entrance`/`courrierComment`/etc.) is carried over
  unchanged from whatever address was already on the cart. **Practical
  effect: switching to a saved address whose street/building differs from
  what's currently on the cart will send the new address's coordinates but
  the old address's display fields** -- a known limitation, not silently
  wrong data (the coordinates, which determine which branch/company can
  serve the order, are correct), but worth fixing properly if this bites in
  practice (would need a saved-address lookup keyed by the resolved
  address's `id`, exposing its full raw record -- a change to
  `address_resolver.py`'s public surface, deliberately out of scope here
  per "reuse `resolve_address` as-is, don't duplicate its logic").
- **A cart context with no existing `address`/`shipments` has no template
  to copy from** and `delivery` fails clearly in that case (prints a
  message, applies nothing) rather than guessing a full postal address from
  scratch. This mirrors issue #20's "narrow scope over an unreliable guess"
  precedent for the promo-swap feature.
- **`shipments[].companyId` is kept from the existing cart shipment**,
  only `branchId` is overridden (to the chosen delivery type's `branchId`
  from `silpo_get_available_delivery_types`). Assumption: one company
  serves all branches for a given account -- every live-observed cart/
  product record in this project so far has shared one `companyId`
  (`1ec88c5d-a050-669c-8467-570a157f3e31` in this session's live account),
  and `silpo_get_available_delivery_types` doesn't return a `companyId` to
  use instead.
- **Update (post-#29 rebase)**: the note originally here described
  `resolve_address()`'s internal `silpo_get_available_delivery_types` call
  as redundant with this module's own separate delivery-type listing call.
  Issue #29 (merged after this ticket was first written) added
  `ResolvedAddress.delivery_types`, exposing that same call's raw response
  for reuse -- `delivery_settings.py` now reads `resolved_address
  .delivery_types` directly instead of making its own call, so the
  duplicate call no longer happens. `resolve_cart_context` is also now
  called with `resolved_address=` (both the pre-apply template fetch and
  the post-apply re-check) so its own no-shipments fallback (#29) reuses
  the same resolved address instead of prompting a second time.
- **Post-apply "newly unavailable" report** cross-references
  `calculation.validations[]` entries where `type == "product"` and
  `context.productId` is present (the shape confirmed by the earlier
  `product.offer.stock.max` example above) against the pre-update cart's
  `products`, matching by `productId`. Any other product-level validation
  `message` is treated the same way (flagged), since no second live example
  of a different `type: "product"` validation message has been observed to
  confirm whether `context.productId` is present on all of them -- if a
  future live run finds one without it, that entry is silently skipped by
  this logic rather than crashing (defensive `isinstance`/`.get()` checks
  throughout `_newly_unavailable`).

## Live-verified: SelfPickup / Nova Poshta address construction (2026-08-05, issue #38)

Issue #38 extended `delivery_settings.py` (issue #37) to support `SelfPickup`
and `NovaPoshta`. Spot-checked live against the real MCP server before
implementing, per `silpo_update_shopping_cart`'s own tool description (see
above) plus `silpo_list_branches`/`silpo_find_nova_poshta_settlements`/
`silpo_find_nova_poshta_offices`'s live responses (see "Location / branch /
delivery tools" above for the full per-tool findings). One real
schema surprise, in the same spirit as issue #37's `"options"` vs
`"deliveryTypes"` find:

- **`silpo_update_shopping_cart`'s own tool description text says the
  SelfPickup address should be built from `branch.cityFull` and
  `branch.addressFull`. Neither field exists on the real
  `silpo_list_branches` response.** The live per-branch shape is
  `{"branchId", "companyId", "externalId", "city", "address", "latitude",
  "longitude", "hasPickup", "open"}` -- `city`/`address`, not
  `cityFull`/`addressFull`. `delivery_settings.py`'s `_self_pickup_address`
  uses the real field names (`city`/`address`), not the tool description's
  literal (but non-existent) ones:
  ```json
  {"addressType": "self-pickup", "city": branch.city,
   "locality": branch.address, "street": branch.address,
   "latitude": branch.latitude, "longitude": branch.longitude}
  ```
- **NovaPoshta's construction rule matched the tool description exactly**
  once checked against real `silpo_find_nova_poshta_settlements`/
  `silpo_find_nova_poshta_offices` responses -- `settlement.title`/`.area`
  and `office.id`/`.latitude`/`.longitude`/`.type`/`.number` are all real
  field names, no correction needed:
  ```json
  {"addressType": "nova-poshta", "city": settlement.title,
   "region": settlement.area, "latitude": String(office.latitude),
   "longitude": String(office.longitude), "officeId": office.id,
   "street": "<Відділення|Поштомат> #<office.number>"}
  ```
  (`office.latitude`/`.longitude` are live-verified as numbers, so the
  tool description's `String(...)` cast is required, unlike
  `silpo_list_branches`, whose lat/lon already come back as strings.)
- **`silpo_list_branches(hasNP=true)` returned exactly 1 branch nationwide**
  in this live account (`branchId: "1ee7fab3-7713-6a0c-b802-8d149aac137a"`,
  city Київ) -- confirms the ticket's assumption that this call resolves a
  single NP-servicing branch/company, not a user pick, unlike
  `hasPickup=true` (311 branches, genuinely needs picking).
- **Both `SelfPickup` and `NovaPoshta` set `shipments[].companyId` from the
  chosen branch**, per the tool description's "Set shipments with the
  branch companyId + branchId" -- unlike `DeliveryHome` (issue #37), which
  keeps the existing cart shipment's `companyId` and only overrides
  `branchId`. Not the same assumption as #37's "one company serves all
  branches" -- the tool description is explicit here, so no assumption was
  needed for these two types.

## Assumptions made in issue #38 (Delivery Settings: SelfPickup / NovaPoshta)

- **SelfPickup branch listing is "nearest of one fetched page," not "nearest
  of all 311 branches."** `silpo_list_branches(hasPickup=true)` is called
  with no explicit `limit` (server default, 50), then the returned page is
  filtered to `open: true` branches with real coordinates (a branch missing
  lat/lon can't be meaningfully distance-ranked, and defaulting to `(0, 0)`
  would falsely rank it as "nearest" -- PR #49 review finding, fixed
  2026-08-05) and sorted client-side by plain squared lat/lon distance to
  the resolved address; the nearest `_NEAREST_PICKUP_BRANCHES` (5) of what's
  left are offered. A branch nearer to the user than anything on that first
  page (possible if the account's default page ordering isn't itself
  distance-sorted) still won't be surfaced -- that part of the gap remains,
  same narrow-scope-over-unreliable-effort precedent as issue #20/#37.
- **`_pick_nova_poshta_branch` prints a visible note if `hasNP=true` ever
  returns more than one branch** (live-verified as exactly 1 for this
  account, see above, but not treated as a hard guarantee) instead of
  silently using `branches[0]` -- PR #49 review finding, fixed 2026-08-05.
  Still uses the first branch either way; the fix is making that choice
  visible, not turning it into a user pick (a real second NP-servicing
  branch has never been observed, so building a picker for it would be
  speculative).
- **Nova Poshta settlement search takes a free-text query from the user**
  (`silpo_find_nova_poshta_settlements({"title": <user input>})`), not a
  city pre-derived from the resolved address -- the tool takes a name
  search, not coordinates, so there's no coordinate-based shortcut
  available the way `SelfPickup`'s nearest-branch sort has one.

## Assumptions made in issue #33 (Favorites Deals / `favorites-deals` command)

`silpo_get_my_favorites` is still not live-verified (see "Profile / account
tools" above -- only its request param names, `{"branchId", "deliveryType",
"timeslotStart", "limit", "offset"}`, are documented from the tool
description, never called live). `favorites_deals.py` assumes its response
wraps the favorited product list under a top-level `"products"` key --
inferred from the tool description's "returns products in the same shape as
`silpo_get_products`" plus the confirmed live shape of the closest sibling
call, `silpo_get_similar_products` (`{"success", "summary", "products": [...],
"meta": {"total"}}`) -- not confirmed against a real response. If the real
key differs (e.g. `"favorites"`), only `list_favorites_deals`'s one `.get("products")`
line needs revisiting. Branch/delivery/timeslot context comes from
`cart_context.resolve_cart_context` called with no pre-resolved address, per
issue #29's "self-resolving path for future callers" note -- this is the
first command to exercise that path live.

## Design decision made in issue #30 (Cart Editor: `cart edit` interactive + `--replace`)

`cart_editor.py` introduces no new schema assumptions -- it reuses three
already-verified tools documented above verbatim: `silpo_remove_cart_products`,
`silpo_add_or_update_cart_products` (both "Cart tools"), and
`silpo_find_products_batch` (same real request/response shape "Product
search / replacement tools" documents, and the same call pattern
`substitution_resolver.py`'s `_check_availability` already uses). One real
design decision was made, not schema-related:

- **`--replace <old-product-id> <new-product-id>`'s new-id semantics**: the
  live API has no per-id product lookup tool (`silpo_get_product_details`
  needs a `slug`, not an id, and nothing produces a `slug` from a bare id).
  So `<new-product-id>` cannot be resolved to the full record
  (`companyId`/`branchId`/`price`) the add call requires by id alone --
  it has to go through search. The chosen design: treat `<new-product-id>`
  as a free-text query to `silpo_find_products_batch` (the same tool the
  interactive flow's search step uses) and match a candidate by exact `id`
  in the results, rather than trusting the top hit. This mirrors
  `substitution_resolver.py`'s own documented fallback (using a raw
  product id as search text when no better query is available) instead of
  inventing a second resolution path. Consequence: `--replace`'s new id
  must be a real, currently-searchable product id (e.g. copied from a
  prior search result, another cart, or an order) -- an id that exists in
  the catalog but doesn't surface for a plain id-as-query search (unlikely
  given ids are UUIDs the search endpoint can presumably match verbatim,
  but unverified live) would be reported as "not found" even though it
  technically exists. This was judged an acceptable, documented limitation
  over adding a second, unverified tool call just for this one flag.
- **No-partial-mutation ordering**: `cli.py`'s `_run_cart_edit_replace`
  resolves the new product (search + exact-id match) *before* calling
  `swap_cart_item`, which itself validates the old id is actually a
  `CartContext.products` line *before* either mutating call. Both the
  interactive and `--replace` paths route through the same
  `swap_cart_item`, so the ordering guarantee (new item confirmed
  resolvable before the old item is ever removed) holds identically for
  both -- see `cart_editor.py`'s module docstring.
- **Quantity preserved across the swap**: the removed line's `quantity` is
  reused for the new line's add call (falling back to `1` if absent),
  rather than always adding `1` -- unlike Cart Writer (#19), which always
  adds fresh Typical Items at `quantity=1` since it has no prior line to
  preserve. `addQuantity: True` is kept for consistency with Cart Writer's
  established payload shape, though since the new line never already
  exists in the cart at add time, `True`/`False` are equivalent here.

## Design decision made in issue #31 (Cart Editor promo-browse path)

No new schema assumptions -- the promo-browse path calls `promo_finder.py`'s
`find_promo_alternatives` exactly as issue #28 built it (same
`silpo_get_similar_products` request/response shape documented above), and
feeds its result into `cart_editor.py`'s `swap_cart_item` exactly as the
free-text path does, after converting the returned `PromoCandidate` into the
same plain product-record shape (`id`/`name`/`price`/`companyId`/`branchId`)
`swap_cart_item` already expects. `cli.py`'s `_run_cart_edit_interactive`
now asks the user to pick free-text search or promo-browse right after
picking which cart item to replace; an item with no discounted alternatives
(empty list, or no `slug` on the cart line at all) prints a message and
falls straight through to the free-text path instead of erroring or
re-prompting for a mode.
