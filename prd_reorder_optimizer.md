## Problem Statement

I do a large share of my grocery shopping by repeating what I've already bought before — the same milk, bread, coffee, cleaning supplies week after week. Today that means manually re-typing or re-searching the same ~15-20 products every time in the Silpo app, checking whether anything's out of stock, and separately checking if a cheaper or promo version exists. It's repetitive and it's easy to forget an item or overpay because I didn't notice a promo.

I also don't want to hand this over to a "smart" system that just re-orders blindly — prices drift, delivery addresses change, and items go out of stock, so the tool needs to surface decisions to me rather than making silent guesses that could add wrong or unwanted items to my cart.

## Solution

A personal CLI tool (`silpo-agent`) that wraps the Silpo MCP server and exposes a `reorder` command. It looks at my recent confirmed online orders, figures out which items I buy consistently, checks whether they're currently available and at what price, and fills my Silpo cart with that "typical" basket — asking me when a decision genuinely needs my input (which delivery address, which substitute when there's more than one option), and never touching checkout/payment itself.

It's built for daily personal use, not a one-off hackathon demo, so it needs to survive repeated real use: persistent auth, a delivery-address flow that doesn't ask me to re-confirm every single run, and clear errors instead of silent wrong guesses when something is missing (no order history, no saved address, empty search results).

A second command, `week` (recipe-plan based weekly cart), is explicitly deferred — the MCP server has no recipe/meal-planning tool, and LLM-freeform ingredient names would need unreliable fuzzy matching against product search. `silpo-agent` is architected as one binary so `week` can be added later without restructuring the auth/MCP-client layer.

## User Stories

1. As a returning Silpo customer, I want to run one command and get my usual groceries added to my cart, so that I don't have to manually re-search the same items every week.
2. As a user, I want the tool to only count items from orders I actually paid for, so that a cart I abandoned or never checked out doesn't distort what it thinks is "typical."
3. As a user, I want to control how many past orders and what frequency threshold define a "typical" item, so that the basket reflects my actual habits, not an arbitrary default.
4. As a user, I want to be told clearly when there's no order history to work from, so that the tool doesn't silently do nothing or guess.
5. As a user, I want the tool to confirm my delivery address before it searches for products, so that the results and prices are for the right store/branch.
6. As a user, I want my most recently confirmed/default saved address proposed first, so that I don't get asked from scratch every single run.
7. As a user, I want to pick a different saved address, or type a brand-new one, if the proposed one is wrong, so that I'm never stuck with an incorrect delivery context.
8. As a user, I want the tool to remember what address I confirmed, so that I (or future-me debugging a bad run) can audit what context a given reorder used.
9. As a user, I want out-of-stock items with exactly one replacement candidate to be substituted automatically, so that I'm not interrupted for obvious cases.
10. As a user, I want to be asked when there's more than one substitute candidate, so that I get to choose instead of the tool guessing what I'd prefer.
11. As a user, I want my past substitution choice remembered, so that I'm not asked the same "which replacement do you want" question every single run for a chronically out-of-stock item.
12. As a user, I want an optional flag to also swap items for cheaper promotional equivalents, so that I don't have to separately go find they've gone on sale.
13. As a user, I want an optional flag to apply available loyalty bonuses/promo codes to the cart, so that I capture discounts I'm already entitled to.
14. As a user, I want promo optimization to be opt-in, not the default, so that a plain reorder repeats what I actually buy rather than substituting things I didn't ask to change.
15. As a user, I want an optional budget cap, so that the tool trims the cheapest-priority typical items to fit if prices have crept up.
16. As a user, I want to see the total cost even without a budget cap, so that I know what the reorder came to before I decide whether to check out.
17. As a user, I want to be warned if my cart already has items in it before the tool adds anything, so that I don't accidentally merge an old session's leftovers with a new reorder without knowing.
18. As a user, I want the tool to only ever fill the cart, never place the order or pay, so that a bug or bad guess can never cost me money without my final manual confirmation in the app.
19. As a user, I want a report after the run showing exactly what was added, what was substituted, and the total, so that I can review before checking out in the app.
20. As a user, I want a local, readable log of past reorder runs, so that I can look back at what the tool did on any given day.
21. As a user, I want my auth token to persist securely between CLI invocations, so that I don't have to re-authenticate through the browser every single time I run the tool.
22. As a user, I want the CLI to be a single binary so that auth and the MCP client are shared infrastructure, ready for a future `week` command without a rewrite.

## Implementation Decisions

**Modules**

- **MCP Client / Auth** — wraps OAuth2.1+PKCE browser login against the Silpo MCP server and all `silpo_*` tool calls behind a single `call(tool, args)` interface. Persists the token in the OS keyring (not a plaintext file) so it survives across CLI invocations. This is shared infrastructure — the eventual `week` command reuses it unchanged.
- **Order Aggregator** — pure function. Input: raw online orders (from `silpo_get_my_online_orders`), a lookback count (`--last N`), and a frequency threshold (`--threshold X`). Output: a list of Typical Items (product id, observed frequency, last known price). No network calls, no side effects — fully unit-testable against fixture order data. If fewer than N orders exist (including zero), the caller surfaces a hard error and exits without touching the cart.
- **Address Resolver** — resolves which branch/delivery context subsequent product search runs against, before any product search happens. Reads `silpo_get_my_delivery_addresses`; always proposes the MCP-marked default/first address for confirmation, regardless of what the Reorder Log recorded on a prior run. On decline, offers the remaining saved addresses, or a new address via `silpo_find_address` → `silpo_get_available_delivery_types`. Writes the confirmed choice to the Reorder Log for audit only — it does not change what gets proposed next run.
- **Substitution Resolver** — input: Typical Items + resolved branch context + Substitution Memory. For each item, checks current availability; if unavailable, calls `silpo_get_replacements`. Exactly one candidate: auto-applies it. More than one: consults Substitution Memory for a prior choice for that item; if none exists, surfaces the choice to the user and records the answer into Substitution Memory for future runs.
- **Promo Optimizer** (invoked only when `--optimize promos` is passed) — two independent actions on the resolved item set: (a) swap a typical item for a cheaper promotional equivalent when one exists, even if the original is in stock; (b) apply available loyalty bonuses/promo codes to the cart as a whole via `silpo_update_shopping_cart`. Off by default.
- **Cart Writer** — before adding anything, reads the current cart (`silpo_get_my_shopping_cart` / `silpo_get_shopping_cart_by_id`); if non-empty, warns the user before proceeding (does not auto-clear or silently merge). Applies the optional `--budget` cap by trimming the lowest-priority typical items until the total fits, if set — otherwise just totals and reports. Calls `silpo_add_or_update_cart_products` to fill the cart and stops there — no checkout/payment call is ever made by this tool.
- **Reorder Log Store** — local, append-only audit trail of past `reorder` runs (items added, substitutions made, confirmed address, total, timestamp) plus persisted Substitution Memory. Read/write only, no business logic — the frequency calculation and address proposal explicitly do not consult it beyond the two documented exceptions (Substitution Memory reuse, audit record of confirmed address).
- **CLI orchestrator** — thin. Parses flags (`--last`, `--threshold`, `--budget`, `--optimize promos`), wires the modules above in sequence: Address Resolver → Order Aggregator → Substitution Resolver → (optional) Promo Optimizer → Cart Writer → report. No independent business logic of its own.

**Architectural decisions**

- Single Python CLI binary; `reorder` ships in v1, `week` (recipe-plan flow) is deferred pending a recipe data source — the MCP server has no recipe/meal-planning tool today.
- Auth token stored in the OS keyring, not a config file.
- Typical-item computation only ever consults confirmed online orders from MCP — local, unpaid cart activity never feeds it, to avoid the tool reinforcing its own unconfirmed suggestions.
- The tool never calls checkout or payment endpoints under any flag combination — order placement stays a manual step in the Silpo app/site.

## Testing Decisions

Good tests here exercise each module's external interface — inputs in, outputs (or recorded side-effecting calls) out — without asserting on internal implementation details, and use fixture data (sample order histories, sample address lists, sample replacement responses) rather than live MCP calls.

Modules to test:

- **Order Aggregator** — pure function, no mocking needed. Cover: normal frequency calculation, threshold boundary (item at exactly X%), N larger than available order count, zero orders.
- **Substitution Resolver** — mock the MCP replacement/availability calls. Cover: zero candidates, exactly one candidate (auto-apply), multiple candidates with no memory (asks), multiple candidates with memory (auto-applies remembered choice).
- **Address Resolver** — mock `silpo_get_my_delivery_addresses` / `silpo_find_address`. Cover: default address accepted, default declined → pick from list, default declined → enter new address, zero saved addresses.
- **Promo Optimizer** — mock promo/coupon MCP calls. Cover: promo swap available vs not, bonus/promo-code application, flag off (no-op passthrough).
- **Cart Writer** — mock cart MCP calls. Cover: empty cart (no warning) vs non-empty cart (warning fires before mutation), budget cap trims correctly, no budget cap just totals.
- **MCP Client / Auth** — mock the OAuth/browser flow and keyring calls rather than driving a real login. Cover: token read from keyring on a warm start, token refresh/re-auth trigger on expiry, first-run flow when no token exists.
- **Reorder Log Store** — cover read/write round-trip and that a corrupt/missing log file doesn't crash a run (starts fresh instead).

No prior art exists in this repo (fresh project) — these will establish the testing pattern going forward, one test module per implementation module, colocated.

## Out of Scope

- `week` (recipe-plan-based weekly cart) command — no MCP recipe/meal tool exists to build it on reliably.
- Checkout, payment, or order placement of any kind.
- `DinnerParty`, `Budget Guardian`, and `Support Ops` concepts from the original idea list.
- Multi-user/family profile handling (`silpo_get_my_family`) — out of scope for v1's single-user reorder flow.
- A preview/dry-run mode before cart mutation — v1 adds directly, then reports what it did.
- Any UI beyond the terminal (no web dashboard, no notifications).

## Further Notes

- The exact schema of `silpo_get_my_online_orders` and `silpo_get_my_delivery_addresses` isn't documented publicly — only the live MCP server's `tools/list` response has the real field definitions. Implementation must inspect this directly rather than assuming field names.
- This PRD covers the Reorder Optimizer flow only. WeekCart is intentionally excluded pending a recipe data source decision.
