# TODO — Reorder Optimizer CLI (from PRD #1)

Legend: 🔴 BLOCKING (other tasks can't start until done) · ⏳ blocked-by noted inline

## 0. Setup — done (#2)
- [x] 🔴 Init Python project skeleton (package layout, dependency manager) — blocks everything below
- [x] 🔴 Inspect live MCP server `tools/list` schema for order objects and delivery-address objects — done via a full live authenticated sweep of all 39 tools (see `docs/mcp_schema.md`), not just `tools/list`; fed the #14/#16-#20 correction tickets below

## 1. MCP Client / Auth — done (#2)
🔴 Blocks every other module — nothing can call MCP without it.
- [x] OAuth2.1+PKCE browser login flow
- [x] OS keyring token storage/retrieval
- [x] Token refresh / re-auth on expiry
- [x] Generic `call(tool, args)` wrapper
- [x] Tests: warm start reads keyring token, refresh triggers on expiry, first-run flow with no token

## 2. Reorder Log Store — done (#2)
No blockers beyond setup.
- [x] Local log file format (append-only, past runs: items added, substitutions, address, total, timestamp)
- [x] Substitution Memory persistence (item → chosen replacement)
- [x] Tests: read/write round-trip, missing/corrupt log file starts fresh instead of crashing

## 3. Order Aggregator — done (#3, field names corrected against live schema in #16)
⏳ Blocked by: task 0 (live schema).
- [x] Typical-item frequency calc from `--last N` / `--threshold X`
- [x] Hard error + no-op exit when order count < N (including zero)
- [x] Tests: threshold boundary, N > available orders, zero orders

## 4. Address Resolver — done (#4, corrected against live schema in #14)
⏳ Blocked by: module 1 (MCP Client), task 0 (live schema).
- [x] Propose default/first saved address for confirmation — note: live data has **no MCP-marked default field**; "first" is just API return order, CONTEXT.md corrected in #14
- [x] Decline → offer remaining saved addresses or new via `silpo_find_address` → `silpo_get_available_delivery_types`
- [x] Write confirmed address to Reorder Log (audit only, doesn't change next proposal)
- [x] Tests: default accepted, decline→pick from list, decline→new address, zero saved addresses

## 5. Substitution Resolver — done (#5, corrected against live schema in #18)
⏳ Blocked by: module 1 (MCP Client), module 2 (Log Store, for memory), module 3 (Order Aggregator output).
- [x] Availability check per typical item — no `silpo_check_availability` tool exists; uses `silpo_find_products_batch`'s `stock`/`available` fields instead (#18)
- [x] Auto-apply when exactly one replacement candidate
- [x] Ask user + persist choice to Substitution Memory when multiple candidates
- [x] Reuse remembered choice on repeat instead of re-asking
- [x] Tests: 0 / 1 / >1 candidates, memory reuse on second run

## 6. Promo Optimizer (optional, `--optimize promos`) — bonus apply done (#7, corrected in #20); swap dropped
⏳ Blocked by: module 5 (Substitution Resolver output), module 1 (MCP Client).
- [ ] ~~Swap typical item for cheaper promo equivalent when available~~ — **dropped**: no real per-product "promo equivalent" tool exists on the live server, only category/promo-code browsing; a name-matching heuristic was judged too unreliable with money on the line (decision + reasoning in `docs/mcp_schema.md`, issue #20)
- [x] Apply loyalty bonuses / promo codes to cart via `silpo_update_shopping_cart` (real payload: `bonusRequested` + `promoCode` on the same call used for delivery-type changes, not a separate tool)
- [x] Off by default — verify no-op when flag absent
- [x] Tests: bonus apply, bonus-apply failure doesn't falsely report success, flag-off passthrough (swap tests n/a — feature dropped)

## 6b. Cart Context Resolution — new module, not in original breakdown (#17)
Needed once live testing showed `silpo_get_my_shopping_cart` returns only a cart id, not contents — every product/cart tool needs `branchId`/`companyId`/`deliveryType`/`timeslot` resolved from a second call. Added as shared infrastructure consumed by modules 5, 6, and 7.
- [x] Two-call resolution: `silpo_get_my_shopping_cart` → `silpo_get_shopping_cart_by_id`
- [x] Wired into `reorder` pipeline right after Address Resolver, before Order Aggregator
- [x] Surfaces `cart.calculation.validations[]` (stock/timeslot errors) to the user, non-blocking
- [x] Tests: normal resolution, empty/fresh cart, cart with validation errors

## 7. Cart Writer — done (#3 happy path, #6 guard+budget, corrected against live schema in #19)
⏳ Blocked by: module 4 (Address Resolver, branch context), module 5/6 (resolved item set), module 1 (MCP Client).
- [x] Non-empty cart guard — warn before mutating, never silent merge/clear
- [x] Optional `--budget` cap: trim lowest-priority items to fit; report total either way
- [x] `silpo_add_or_update_cart_products` call — cart only, never checkout/payment (real payload needs `productId`+`companyId`+`branchId` per item, fixed in #19); plastic-bag-style items filtered before write
- [x] Post-run report: added items, substitutions, total
- [x] Tests: empty vs non-empty warn, budget trim, no-cap totals-only

## 8. CLI Orchestrator — done, wired across #3–#7
🔴⏳ Blocked by: ALL of modules 1–7 — last thing built, wires the rest.
- [x] Flag parsing: `--last`, `--threshold`, `--budget`, `--optimize promos`
- [x] Pipeline wiring: Address Resolver → Cart Context Resolver → Order Aggregator → Substitution Resolver → (Promo Optimizer) → Cart Writer → report
- [x] No independent business logic beyond wiring

## Out of scope (do not schedule)
`week` command, checkout/payment, DinnerParty/Budget Guardian/Support Ops, multi-user/family profiles, preview/dry-run mode, non-terminal UI.
