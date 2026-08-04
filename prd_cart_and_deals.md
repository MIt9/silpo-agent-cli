## Problem Statement

`reorder` fills my cart well, but it's a one-way street: I can't see what's actually in the cart afterward without leaving the CLI, I can't swap out an individual item if I don't want it, and I have no way to know if something I'm about to buy — or already have in my favorites — is currently on sale. Right now the only way to check any of that is to poke at the MCP tools directly by hand.

I also want a way to just browse what's discounted right now, independent of any specific cart or reorder run — a "what's a good deal today" check, and a "are any of my favorites currently cheaper" check, since those are two different questions I ask myself at different times.

## Solution

Four new read-facing capabilities, plus one write capability, all built on the same `MCPClient`/`CartContext` infrastructure `reorder` already uses:

- `cart` — shows what's currently in the real Silpo cart: items, stock, total (the amount actually paid, not pre-discount), any validation errors/warnings, and the loyalty bonus balance.
- `cart edit` — lets me manually swap one cart item for another, either by free-text search or by browsing real promo alternatives for that specific item (via Silpo's own similar-products engine, not a guess).
- `cart promos` — for every item currently in the cart, shows real promo alternatives (same similar-products mechanism as `cart edit`), purely informational — I decide what to do about it via `cart edit`.
- `deals` — independent of my cart entirely: scans active promotions across categories and shows the best current discounts.
- `favorites-deals` — checks my own favorites list for items that are currently discounted.

## User Stories

1. As a user, I want to see what's currently in my Silpo cart from the CLI, so that I don't have to open the app just to check.
2. As a user, I want the cart view to show the amount I'll actually pay (after discounts), not the pre-discount total, so that I'm not misled about cost.
3. As a user, I want to see any cart validation problems (stock issues, a stale delivery timeslot) when I check my cart, so that I know before I try to check out in the app.
4. As a user, I want to see my available loyalty bonus balance alongside my cart, so that I know if it's worth applying.
5. As a user, I want to replace a specific item in my cart with something else, so that I'm not stuck with whatever `reorder` picked.
6. As a user, I want to search for a replacement item by name when I know roughly what I want, so that I'm not limited to promo suggestions.
7. As a user, I want to see real promo alternatives for a specific cart item I'm replacing, so that I can pick something genuinely similar and cheaper, not something the tool merely guessed was related by name.
8. As a user, I want a non-interactive way to replace one item with another (old id → new id) without going through prompts, so that I can script it if I already know exactly what I want.
9. As a user, I want to see promo alternatives for every item currently in my cart at once, without having to check each one individually, so that I can quickly spot which items are worth swapping.
10. As a user, I want that promo-alternatives check to be purely informational — it should never swap anything on its own — so that I stay in control of what changes in my cart.
11. As a user, I want to browse what's on sale right now across the store, independent of what's in my cart, so that I can discover good deals I wasn't specifically looking for.
12. As a user, I want that deals browse sorted by how big the discount actually is, so that the best deals surface first instead of an arbitrary list.
13. As a user, I want to control how many deals are shown, so that I can see more or fewer depending on how much time I have.
14. As a user, I want to check whether any of my favorite products are currently discounted, so that I know when it's a good time to buy something I already like.
15. As a user, I want all of these to work against my real Silpo account and real cart, the same way `reorder` already does, so that the information is trustworthy and actionable, not a simulation.

## Implementation Decisions

**Modules**

- **Cart Viewer** — thin. Formats an already-resolved `CartContext` (extended with the `products`/`validations`/`bonus_available` fields `reorder`'s pipeline already populates) into a readable report: items with quantity/price/stock, the amount actually payable (`totalAfterDiscounts`, never the pre-discount `total`), validation messages, and the bonus balance. No new MCP calls beyond what `CartContext` resolution already makes.
- **Similar-Products Promo Finder** — deep, shared by `cart edit`'s promo path and `cart promos`. Given a product's `slug` (cart items already carry their own `slug`, confirmed live), calls `silpo_get_similar_products` and filters the results to ones currently discounted (`oldPrice` present and higher than `price`), returning them ranked by discount size. This replaces an earlier, rejected approach of scanning promo categories and keyword-matching product names against cart item names — confirmed live that no product record anywhere in this API carries a category id, so keyword matching would have been a weak heuristic; Silpo's own similarity engine is a real signal instead.
- **Promo Scanner** — deep, used only by `deals` (not by `cart edit`/`cart promos` — those use the Similar-Products Promo Finder instead, since they start from a specific item). Given no per-item starting point, `deals` needs to discover discounts store-wide: iterates the active promotion categories (`silpo_get_promotions`), pulls a capped number of products per category (confirmed live some categories list thousands of products — capping is necessary, not optional), merges the results, and computes each product's discount percentage from `price`/`oldPrice`.
- **Cart Editor** — deep, the only capability here that mutates the real cart. Given an old product id and a new product's full record (id/company id/branch id/price), removes the old item and adds the new one. Offers two ways to find the replacement: free-text search (reusing the same product-search tool the rest of the codebase already uses), or browsing the Similar-Products Promo Finder's output for the item being replaced. Supports both an interactive flow (numbered list of current cart items → pick one → pick a replacement path → confirm) and a non-interactive `--replace <old-product-id> <new-product-id>` flag that performs the same swap without prompts.
- **Favorites Deals** — thin. Calls the favorites list, filters to currently-discounted items the same way the Similar-Products Promo Finder does (`oldPrice` higher than `price`), no matching/heuristic needed since it's already the user's own explicit list.
- **CLI orchestrator** — extends the existing `silpo-agent` command with `cart` (default: view), `cart edit`, `cart promos`, `deals` (`--limit`, default 10), and `favorites-deals`. Reuses the existing `CartContext` resolution rather than re-implementing it.

**Architectural decisions**

- All five capabilities reuse the existing `MCPClient`/`CartContext` infrastructure `reorder` already established — no new auth or cart-context resolution logic.
- `cart edit` and its non-interactive `--replace` flag are the only new capability that mutates the real cart; everything else (`cart`, `cart promos`, `deals`, `favorites-deals`) is strictly read-only.
- Considered and explicitly rejected: giving items on the user's favorites list special priority in `reorder`'s existing typical-item selection. Out of scope for this PRD and not planned.
- The existing plastic-bag filter (`cart_writer.py`'s `_is_plastic_bag`, name-based, already used by `reorder`) extends to every read-facing list this PRD introduces — `deals`, `cart promos`, and the free-text search results inside `cart edit`. Bags are irrelevant to all of these: they're added automatically when the order is assembled, never something the user needs to search for, discover a deal on, or manually pick as a replacement.

## Testing Decisions

Same discipline as the existing modules: tests exercise each module's external interface (inputs in, outputs or recorded MCP calls out) against fixture data matching the real, live-verified schemas in `docs/mcp_schema.md`, not live network calls.

All five modules get tests:

- **Cart Viewer** — formats a resolved `CartContext` correctly: items/total/validations/bonus all appear, `totalAfterDiscounts` is used (not `total`), an empty cart and a cart with validation errors both render sensibly.
- **Similar-Products Promo Finder** — given a slug, filters `silpo_get_similar_products` results to genuinely discounted ones only, ranks correctly, and handles a slug with no discounted matches (empty result, not a crash).
- **Promo Scanner** — caps per-category correctly, merges across multiple categories, computes discount percentage correctly, and handles a category returning fewer than the cap.
- **Cart Editor** — free-text search path and promo-browse path both produce the same underlying swap call sequence (remove old, add new); the non-interactive `--replace` flag performs the same mutation without any prompts; an invalid/not-found replacement doesn't silently corrupt the cart.
- **Favorites Deals** — filters correctly to discounted-only items; a favorites list with nothing currently discounted returns empty rather than erroring.

## Out of Scope

- Automatic promo swapping without user confirmation — every swap in `cart edit` (including the promo path) is a deliberate user choice, never automatic.
- Giving favorites special priority in `reorder`'s typical-item selection (considered during grilling, explicitly rejected).
- Checkout/payment — unchanged from the existing `reorder` scope; none of these five capabilities touch it either.
- Any category-based matching for promo alternatives — confirmed live that no product record carries a category id, so this path isn't available and isn't pursued.
- Editing more than one cart item per `cart edit` invocation — each run handles a single swap; scripting multiple swaps means multiple `--replace` invocations.

## Further Notes

- `silpo_get_similar_products`'s exact discount-signal fields (`oldPrice` vs `specialPrices`) should be spot-checked against a live account with more current promotions than were available during this PRD's discovery session, since the account used had limited live promo activity at the time.
- `docs/mcp_schema.md` should be extended with a "Live-verified: similar-products and promotions tools" section once these modules are built and tested against the real server, following this project's existing convention of recording per-issue schema assumptions and corrections.
