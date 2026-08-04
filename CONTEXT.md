# silpo-agent-cli

CLI wrapper over the Silpo MCP server (https://mcp.silpo.ua/mcp) that automates shopping-cart tasks a user would otherwise do by hand in the app.

## Language

**Reorder flow**:
The subcommand that rebuilds a cart from the user's historical online orders rather than from a hand-typed list. It aggregates a set of recent orders, derives the user's typical items, checks current availability, applies substitutions and promo-aware optimization, and assembles a cart. It does not invent new products or meals — it repeats and refines what the user already buys.
_Avoid_: Reorder Optimizer CLI (product-idea name, not the domain term)

**Typical item**:
A product considered part of the user's regular purchase pattern, determined by its frequency across a set of the user's recent online orders (appears in at least a configurable share of them). Distinct from a *Favorite*, which is an explicit like the user set directly, independent of purchase history.
_Avoid_: recurring item, regular purchase

**Substitution decision**:
The rule for handling a typical item that is currently unavailable. Auto-substituted when exactly one replacement candidate exists (via `silpo_get_replacements`); otherwise the user is asked to choose. Distinct from *promo optimization*, which swaps an available item for a cheaper promotional alternative even when the original is in stock.

**Promo optimization**:
Applying promotions to a reorder cart in two ways: swapping a typical item for a discounted equivalent, and applying available bonuses/promo codes to the cart as a whole. Optional — invoked explicitly, not the reorder default.

**Non-empty cart guard**:
The rule that the Reorder flow always warns before mutating the cart if it already contains items from a prior session, rather than silently adding on top or clearing it.

**Reorder flow — cart-only scope**:
The Reorder flow stops at filling the cart; it never calls checkout or payment. Order placement stays a manual step in the Silpo app/site.

**Reorder log**:
A local, cross-run audit trail of past Reorder flow executions (items added, substitutions made, sums, timestamps). Read-only history for the user — it does not itself feed the typical-item calculation. Only confirmed purchases (from `silpo_get_my_online_orders`) count as typical-item signal, since the Reorder flow only fills the cart and the user may never pay for what was added.

**Substitution memory**:
A local record of past substitution choices (typical item X was unavailable, user picked replacement Y). Reused on later runs: if X is unavailable again with the same multiple-candidate situation, Y is auto-applied instead of asking again.

**Delivery address resolution**:
The step, run before product search, that establishes which address/branch context the Reorder flow searches and prices against. Source is `silpo_get_my_delivery_addresses` (never order data). Live-verified (2026-08-04, see `docs/mcp_schema.md`): MCP does **not** mark any address as a default — there is no `is_default` field. "Propose first" therefore just means the first address in the API's return order, an assumption documented here rather than a server-marked default. That first address is always proposed for confirmation first, every run. If declined, the user picks from the remaining saved addresses or enters a new one via `silpo_find_address` (geocoded, no saved `id`). The confirmed choice is written to the Reorder log for audit, but doesn't change what's proposed next run — the API's return order still wins. This precedes typical-item search because branch/delivery context (resolved via `silpo_get_available_delivery_types`, called with the resolved address's lat/lon) determines product availability and pricing.

**Cart context resolution**:
The step, run right after Delivery address resolution and before typical-item search, that resolves the real cart/delivery context (`shoppingCartId`, `branchId`, `companyId`, `deliveryType`, `timeslot`) most product-facing MCP tools require. Two calls: `silpo_get_my_shopping_cart` (returns only a `shoppingCartId`) then `silpo_get_shopping_cart_by_id` (returns the actual cart). Any `cart.calculation.validations[]` (e.g. a stale timeslot, an out-of-stock item) is surfaced to the user, never silently dropped, but doesn't block the run.

**WeekCart flow**:
A recipe-plan-based basket generation concept — invent a week of meals, then map ingredients to products. Out of scope: the MCP server has no recipe/meal-planning tool, and LLM-freeform ingredient names would need unreliable fuzzy matching against product search.
