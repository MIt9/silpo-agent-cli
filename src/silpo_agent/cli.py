"""silpo-agent CLI entrypoint. `reorder` wires the pipeline together per the
PRD's module order: Address Resolver -> Cart Context Resolver -> Order
Aggregator -> Substitution Resolver -> (optional) Promo Optimizer -> Cart
Writer -> report. Cart Context Resolver resolves branchId/companyId/
deliveryType/timeslot/products/bonus_available/raw timeslot-address-shipments
(see cart_context.py); its `CartContext` is threaded through to Substitution
Resolver (issue #18, for its availability/replacement lookups), Promo
Optimizer (issue #20, for its bonus-application `silpo_update_shopping_cart`
call), and Cart Writer (issue #19, as the source of truth for the
non-empty-cart guard and the branch/company fallback on each add-to-cart
item). Cart Writer owns the non-empty cart guard (warn-and-proceed-or-abort)
and the optional `--budget` trim; the CLI only parses `--budget` and reports
the outcome. Promo Optimizer runs only when `--optimize promos` is passed --
the module isn't even called otherwise, so a plain `reorder` makes zero
promo-related MCP calls (CONTEXT.md's "Promo optimization" entry).
"""

import argparse
import sys
from importlib.metadata import version

from silpo_agent.address_resolver import resolve_address
from silpo_agent.auth import MCPClient
from silpo_agent.cart_context import resolve_cart_context
from silpo_agent.cart_editor import (
    add_cart_item,
    CartEditError,
    remove_cart_item,
    resolve_product_by_slug,
    search_replacement_candidates,
    set_cart_item_quantity,
    swap_cart_item,
)
from silpo_agent.cart_viewer import format_cart
from silpo_agent.cart_writer import write_cart
from silpo_agent.coupons_lister import list_coupons
from silpo_agent.delivery_settings import run_delivery_settings
from silpo_agent.favorites_deals import list_favorites_deals
from silpo_agent.log_store import ReorderLogStore
from silpo_agent.order_aggregator import InsufficientOrderHistoryError, derive_typical_items
from silpo_agent.promo_finder import find_promo_alternatives
from silpo_agent.promo_optimizer import optimize_promos
from silpo_agent.promo_scanner import CategoryNotFoundError, scan_deals
from silpo_agent.substitution_resolver import resolve_substitutions


def _run_reorder(
    last: int, threshold: float, client, log_store, budget: float | None = None, optimize: str | None = None
) -> int:
    address = resolve_address(client, log_store)
    if address is None:
        print("reorder: no delivery address resolved; aborting before product search", file=sys.stderr)
        return 1

    # Pass the already-resolved address through so Cart Context Resolver's
    # no-shipments fallback (issue #29) reuses it instead of prompting again.
    cart_context = resolve_cart_context(client, resolved_address=address, log_store=log_store)

    orders_response = client.call("silpo_get_my_online_orders", {"limit": min(last, 100)}) or []
    orders = orders_response.get("orders", []) if isinstance(orders_response, dict) else orders_response
    try:
        typical_items = derive_typical_items(orders, last=last, threshold=threshold)
    except InsufficientOrderHistoryError as exc:
        print(f"reorder: {exc}", file=sys.stderr)
        return 1

    substitution_result = resolve_substitutions(client, log_store, typical_items, cart_context)

    items = substitution_result.items
    promo_result = None
    if optimize == "promos":
        promo_result = optimize_promos(client, items, cart_context)
        items = promo_result.items

    report = write_cart(client, items, cart_context, budget=budget)

    if address:
        print(f"Delivering to: {address.label}")
    for original_id, replacement_id in substitution_result.substitutions:
        print(f"Substituted {original_id} -> {replacement_id}")
    if substitution_result.unavailable:
        print(f"Unavailable ({len(substitution_result.unavailable)}): {', '.join(substitution_result.unavailable)}")
    if promo_result is not None and promo_result.bonus_applied:
        print(f"Applied {promo_result.bonus_applied:.2f} bonus points to cart")

    if report.aborted:
        return 1

    if report.trimmed:
        print(f"Trimmed {len(report.trimmed)} item(s) to fit budget {budget:.2f}:")
        for product_id, price in report.trimmed:
            print(f"  - {product_id}: {price:.2f}")
    print(f"Added {len(report.items_added)} item(s):")
    for product_id, price in report.items_added:
        print(f"  - {product_id}: {price:.2f}")
    print(f"Total: {report.total:.2f}")
    print("Check your cart: https://silpo.ua/basket")
    return 0


def _run_delivery(client, log_store, input_fn, print_fn) -> int:
    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=print_fn)
    return 0 if result.applied else 1


def _run_cart_edit_replace(client, cart_context, old_slug, new_slug, print_fn) -> int:
    """Non-interactive swap (issue #50): both arguments are slugs. The new
    one resolves through `cart_editor.resolve_product_by_slug`, a real
    per-slug lookup -- the old path used the id as a free-text search query,
    which issue #18 documents as usually returning nothing."""
    new_product = resolve_product_by_slug(client, cart_context, new_slug)
    if new_product is None:
        print_fn(f"cart edit: replacement product {new_slug!r} not found")
        return 1
    try:
        result = swap_cart_item(client, cart_context, old_slug, new_product)
    except CartEditError as exc:
        print_fn(f"cart edit: {exc}")
        return 1
    print_fn(f"Replaced {result.removed_slug} with {result.added_slug} ({result.added_price:.2f})")
    return 0


def _run_cart_edit_add(client, cart_context, new_slug, print_fn, quantity: float = 1) -> int:
    """Non-interactive add: adds NEW_SLUG as a brand-new cart line (quantity
    --quantity, default 1), without removing anything. Errors (no MCP
    mutation made) if the slug doesn't resolve, is already in the cart, or
    --quantity isn't a positive number -- see add_cart_item."""
    if quantity <= 0:
        print_fn(f"cart edit: --quantity must be a positive number, got {quantity}")
        return 1
    new_product = resolve_product_by_slug(client, cart_context, new_slug)
    if new_product is None:
        print_fn(f"cart edit: product {new_slug!r} not found")
        return 1
    try:
        result = add_cart_item(client, cart_context, new_product, quantity=quantity)
    except CartEditError as exc:
        print_fn(f"cart edit: {exc}")
        return 1
    quantity_display = int(quantity) if quantity == int(quantity) else quantity
    print_fn(f"Added {result.added_slug} x{quantity_display} ({result.added_price:.2f} each)")
    return 0


def _run_cart_edit_qty(client, cart_context, slug, quantity_str: str, print_fn) -> int:
    """Non-interactive: sets SLUG's existing cart line quantity to the
    absolute value NUM (a set, not a delta -- unlike --add's addQuantity=True
    mechanism), zero prompts. Errors (no MCP mutation made) if NUM isn't a
    positive number, or if SLUG isn't already a line in the cart -- see
    set_cart_item_quantity."""
    try:
        quantity = float(quantity_str)
    except ValueError:
        print_fn(f"cart edit: --qty quantity must be a number, got {quantity_str!r}")
        return 1
    if quantity <= 0:
        print_fn(f"cart edit: --qty quantity must be a positive number, got {quantity}")
        return 1
    try:
        result = set_cart_item_quantity(client, cart_context, slug, quantity)
    except CartEditError as exc:
        print_fn(f"cart edit: {exc}")
        return 1
    old_display = int(result.old_quantity) if result.old_quantity == int(result.old_quantity) else result.old_quantity
    new_display = int(result.new_quantity) if result.new_quantity == int(result.new_quantity) else result.new_quantity
    line_total = result.added_price * result.new_quantity
    print_fn(f"{result.added_slug}: {old_display} -> {new_display} ({line_total:.2f})")
    return 0


def _run_cart_edit_remove(client, cart_context, slug, print_fn) -> int:
    """Non-interactive delete: removes SLUG's cart line, zero prompts,
    nothing added back. Errors (no MCP mutation made) if the slug isn't
    actually in the cart -- see remove_cart_item."""
    try:
        result = remove_cart_item(client, cart_context, slug)
    except CartEditError as exc:
        print_fn(f"cart edit: {exc}")
        return 1
    print_fn(f"Removed {result.removed_slug} ({result.removed_price:.2f})")
    return 0


def _pick_free_text_replacement(client, cart_context, input_fn, print_fn) -> dict | None:
    """Free-text search path (issue #30): prompts for search terms, shows
    matching candidates, lets the user pick one. Returns the chosen product
    record, or None (with an explanatory message already printed) if the
    search comes up empty or the pick is out of range."""
    query = input_fn("Search for a replacement: ").strip()
    candidates = search_replacement_candidates(client, cart_context, query) if query else []
    if not candidates:
        print_fn(f"cart edit: no results for {query!r}")
        return None

    print_fn("Candidates:")
    for i, candidate in enumerate(candidates, start=1):
        print_fn(f"{i}. {candidate.get('name')} ({candidate.get('price')})")
    pick = input_fn("Pick a replacement: ").strip()
    pick_idx = int(pick) if pick.isdigit() else None
    if not pick_idx or not (1 <= pick_idx <= len(candidates)):
        print_fn(f"No candidate numbered {pick!r}.")
        return None
    return candidates[pick_idx - 1]


def _pick_promo_replacement(candidates, input_fn, print_fn) -> dict | None:
    """Promo-browse path (issue #31): shows the (already non-empty) list of
    `PromoCandidate`s from `find_promo_alternatives` and lets the user pick
    one, converted to the plain product-record shape `swap_cart_item`
    expects. Returns None (with a message already printed) if the pick is
    out of range."""
    print_fn("Promo alternatives:")
    for i, candidate in enumerate(candidates, start=1):
        print_fn(f"{i}. {candidate.name}: {candidate.price:.2f} (was {candidate.old_price:.2f})")
    pick = input_fn("Pick a promo alternative: ").strip()
    pick_idx = int(pick) if pick.isdigit() else None
    if not pick_idx or not (1 <= pick_idx <= len(candidates)):
        print_fn(f"No candidate numbered {pick!r}.")
        return None
    chosen = candidates[pick_idx - 1]
    return {
        "id": chosen.product_id,
        "slug": chosen.slug,
        "name": chosen.name,
        "price": chosen.price,
        "companyId": chosen.company_id,
        "branchId": chosen.branch_id,
    }


def _run_cart_edit_interactive(client, cart_context, input_fn, print_fn) -> int:
    if not cart_context.products:
        print_fn("Your cart is empty; nothing to edit.")
        return 0

    print_fn("Current cart items:")
    for i, product in enumerate(cart_context.products, start=1):
        print_fn(f"{i}. {product.get('name') or product.get('productId')}")
    choice = input_fn("Pick an item to replace: ").strip()
    idx = int(choice) if choice.isdigit() else None
    if not idx or not (1 <= idx <= len(cart_context.products)):
        print_fn(f"No item numbered {choice!r}.")
        return 1
    old_product = cart_context.products[idx - 1]
    old_slug = old_product.get("slug")

    mode = input_fn("Replace via [1] free-text search or [2] promo alternatives? [1/2] ").strip()
    if mode == "2":
        promo_candidates = find_promo_alternatives(client, cart_context, old_slug) if old_slug else []
        if promo_candidates:
            chosen = _pick_promo_replacement(promo_candidates, input_fn, print_fn)
        else:
            print_fn("cart edit: no discounted alternatives found for this item; falling back to free-text search.")
            chosen = _pick_free_text_replacement(client, cart_context, input_fn, print_fn)
    else:
        chosen = _pick_free_text_replacement(client, cart_context, input_fn, print_fn)

    if chosen is None:
        return 1

    old_label = old_product.get("name") or old_slug
    new_label = chosen.get("name") or chosen.get("slug") or chosen.get("id")
    confirm = input_fn(f"Replace {old_label} with {new_label}? [y/N] ").strip().lower()
    if confirm not in ("y", "yes"):
        print_fn("Aborted: cart left unchanged.")
        return 0

    try:
        result = swap_cart_item(client, cart_context, old_slug, chosen)
    except CartEditError as exc:
        print_fn(f"cart edit: {exc}")
        return 1
    # Free-text search results aren't guaranteed to carry a slug, so fall
    # back to the label already shown to the user rather than printing "None".
    print_fn(f"Replaced {result.removed_slug} with {result.added_slug or new_label} ({result.added_price:.2f})")
    return 0


def _run_cart_edit(
    client,
    log_store,
    input_fn,
    print_fn,
    replace: list[str] | None,
    add: str | None = None,
    quantity: float = 1,
    qty: list[str] | None = None,
    remove: str | None = None,
) -> int:
    cart_context = resolve_cart_context(client, input_fn=input_fn, print_fn=print_fn, log_store=log_store)
    if not cart_context.shopping_cart_id:
        print_fn("cart edit: no cart resolved; nothing to edit")
        return 1

    if replace is not None:
        old_id, new_id = replace
        return _run_cart_edit_replace(client, cart_context, old_id, new_id, print_fn)

    if add is not None:
        return _run_cart_edit_add(client, cart_context, add, print_fn, quantity=quantity)

    if qty is not None:
        slug, new_qty = qty
        return _run_cart_edit_qty(client, cart_context, slug, new_qty, print_fn)

    if remove is not None:
        return _run_cart_edit_remove(client, cart_context, remove, print_fn)

    return _run_cart_edit_interactive(client, cart_context, input_fn, print_fn)


def _run_clear_context(log_store, input_fn, print_fn) -> int:
    answer = input_fn(
        "This will permanently delete your local reorder history and substitution memory. Continue? [y/N] "
    ).strip().lower()
    if answer not in ("y", "yes"):
        print_fn("Aborted: local data left unchanged.")
        return 0
    log_store.clear()
    print_fn("Cleared local reorder history and substitution memory.")
    return 0


def _run_cart(client, log_store, input_fn, print_fn) -> int:
    cart_context = resolve_cart_context(client, input_fn=input_fn, log_store=log_store, print_fn=print_fn)
    for line in format_cart(cart_context):
        print_fn(line)
    return 0


def _run_deals(client, limit, log_store, input_fn, print_fn, category=None) -> int:
    cart_context = resolve_cart_context(client, log_store=log_store, input_fn=input_fn, print_fn=print_fn)
    try:
        deals = scan_deals(client, cart_context, limit=limit, category=category)
    except CategoryNotFoundError as exc:
        print_fn(f"deals: no category matching {str(exc)!r} found")
        return 1
    if not deals:
        print_fn("No current deals found.")
        return 0
    for deal in deals:
        line = f"{deal.name}: {deal.price:.2f} (was {deal.old_price:.2f}, -{deal.discount_pct:.0f}%)"
        # Issue #50: the slug is what `cart edit --replace` takes, so a deal
        # is only actionable with it printed. Omitted when absent.
        if deal.slug:
            line += f"  {deal.slug}"
        print_fn(line)
    return 0


def _run_coupons(client) -> int:
    lines = list_coupons(client)
    if not lines:
        print("No active coupons found.")
        return 0
    for line in lines:
        print(line.format())
    return 0


def _run_favorites_deals(client, log_store, input_fn, print_fn) -> int:
    deals = list_favorites_deals(client, log_store, input_fn=input_fn, print_fn=print_fn)
    if not deals:
        print_fn("No discounted favorites found.")
        return 0
    for deal in deals:
        print_fn(deal.format())
    return 0


def _run_cart_promos(client, log_store, input_fn, print_fn) -> int:
    """`cart promos` (issue #28): read-only -- for every item currently in
    the cart, shows real promo alternatives via the Similar-Products Promo
    Finder (promo_finder.py). Never calls a cart-mutating tool."""
    cart_context = resolve_cart_context(client, input_fn=input_fn, log_store=log_store, print_fn=print_fn)
    if not cart_context.products:
        print_fn("Your cart is empty.")
        return 0

    for item in cart_context.products:
        name = item.get("name") or item.get("productId") or "?"
        slug = item.get("slug")
        # Issue #50: both halves of a swap must be addressable -- the cart
        # item's slug is `--replace`'s *old* argument, each alternative's is
        # the *new* one.
        print_fn(f"{name}:  {slug}" if slug else f"{name}:")
        candidates = find_promo_alternatives(client, cart_context, slug) if slug else []
        if not candidates:
            print_fn("  no discounted alternatives found.")
            continue
        for candidate in candidates:
            line = (
                f"  - {candidate.name}: {candidate.price:.2f} (was {candidate.old_price:.2f}, "
                f"-{candidate.discount:.2f})"
            )
            if candidate.slug:
                line += f"  {candidate.slug}"
            print_fn(line)
    return 0


_TOP_LEVEL_EPILOG = """\
First run triggers a one-time browser login (OAuth2.1+PKCE against
mcp.silpo.ua); the token is cached in your OS keyring afterward, so
later runs don't re-prompt until it expires.

commands:
  cart            show your current real cart: items, payable total, bonus balance (read-only)
  cart edit       manually replace one cart item with another, or add a new one
  cart promos     show real promo alternatives for every item in your cart (read-only)
  reorder         rebuild your cart from your typical (frequently-bought) items
  delivery        explicitly set your delivery address, delivery type, and timeslot
  clear-context   wipe your local reorder history and substitution memory
  coupons         list your active loyalty coupons (read-only)
  deals           show the best current store-wide discounts (read-only)
  favorites-deals list your favorited products currently on discount (read-only)

run 'silpo-agent reorder --help' for reorder's flags and examples.
"""

_CLEAR_CONTEXT_EPILOG = """\
deletes the local Reorder Log and Substitution Memory (~/.silpo-agent/
reorder_log.json) after asking for confirmation -- declining leaves
everything untouched. Purely local state: never touches the OS keyring
auth token, your real Silpo cart, or the Silpo servers (no MCP calls).
"""

_DELIVERY_EPILOG = """\
what it does, in order:
  1. confirms your delivery address (same address confirmation as reorder --
     proposes the first saved address, or pick a different one/type a new one)
  2. lists delivery types available at that address and asks you to pick one
     -- DeliveryHome (regular home delivery), SelfPickup, and NovaPoshta are
     supported end to end; picking anything else stops here without
     changing anything
       - SelfPickup: lists the nearest pickup branches to your address and
         asks you to pick one
       - NovaPoshta: asks for a city/settlement to search, then an office or
         parcel locker in it
  3. lists real available timeslots at that delivery type's branch and asks
     you to pick one
  4. applies the address, delivery type, and timeslot together in a single
     update to your real Silpo cart
  5. reports which items already in your cart are now unavailable under the
     new delivery context -- purely informational, nothing is removed or
     swapped automatically

interactive only -- no flags. an invalid or out-of-range choice at any step
aborts cleanly without applying anything.
"""

_CART_EDIT_EPILOG = """\
what it does, in order (interactive, no flags):
  1. lists the items currently in your real cart, numbered
  2. asks which one to replace
  3. asks how to find the replacement:
       [1] free-text search -- shows matching candidates (plastic bags are
           never shown as candidates)
       [2] promo alternatives -- reuses `cart promos`'s Similar-Products
           Promo Finder for the specific item being replaced, showing only
           genuinely discounted candidates. If it finds none, falls back to
           the free-text search instead of erroring.
  4. asks you to confirm the swap
  5. removes the old item and adds the new one (silpo_remove_cart_products
     then silpo_add_or_update_cart_products -- there's no in-place "update
     this line" call) -- the old item is never removed until the new one is
     confirmed to exist, so a failed/declined swap always leaves the cart
     untouched

non-interactive scripting:
  silpo-agent cart edit --replace <old-slug> <new-slug>
performs the same swap with zero prompts. both arguments are product slugs,
exactly as printed by `cart`, `deals`, `favorites-deals` and `cart promos` --
copy one from there rather than constructing it, slugs are generated by Silpo
and cannot be derived from a product name.

<old-slug> is matched against your cart's own lines, locally, with no network
call. <new-slug> is resolved via silpo_get_product_details, a real per-slug
lookup that also carries back the companyId/branchId the cart write needs.

an old slug not actually in your cart, or a new slug that resolves to
nothing, errors clearly and leaves the cart untouched.

adding a brand-new item, no swap:
  silpo-agent cart edit --add <new-slug> [--quantity NUM]
adds <new-slug> as a new cart line (quantity 1, or --quantity -- accepts a
number like 0.5 for weighted products), without
removing anything. mutually exclusive with --replace; --quantity is only
valid together with --add. errors instead of touching the cart if
<new-slug> is already a line in your cart -- rerunning --add would otherwise
have to guess whether you wanted +N or to leave it alone; use --replace or
reorder for an item already in your cart.

changing the quantity of an item already in your cart:
  silpo-agent cart edit --qty <slug> <num>
sets that cart line's quantity to <num> -- an absolute set, not a delta (so
--qty milk 3 always leaves you with 3, regardless of what was there before).
<num> accepts a number like 0.5 or 1.5 for weighted products. mutually
exclusive with --replace/--add/--remove. errors instead of touching the cart
if <slug> isn't already a line in it -- use --add to add it first.

removing an item, no replacement:
  silpo-agent cart edit --remove <slug>
removes <slug>'s cart line, zero prompts, nothing added back. mutually
exclusive with --replace/--add/--qty. errors instead of touching the cart if
<slug> isn't actually a line in your cart.
"""

_REORDER_EPILOG = """\
what it does, in order:
  1. confirms your delivery address (proposes the first saved address;
     you can pick a different one or type a new one)
  2. looks at your last --last online orders, keeps items that appear in
     at least --threshold share of them ("typical items")
  3. checks each typical item is still in stock; auto-substitutes when
     there's exactly one replacement, asks you when there's more than one
     (and remembers your answer for next time)
  4. if --optimize promos was passed: applies any available loyalty
     bonuses to the cart
  5. warns you before touching a non-empty cart (never silently merges
     or clears it) -- decline aborts with the cart untouched
  6. if --budget was passed: trims your least-frequent typical items
     until the total fits
  7. adds the final item set to your real Silpo cart and prints a report

it only ever fills the cart -- it never checks out or pays. that step is
always manual, in the Silpo app or on silpo.ua.

examples:
  # typical items = bought in at least half of your last 10 orders
  silpo-agent reorder --last 10 --threshold 0.5

  # same, but cap the total spend at 1500 UAH
  silpo-agent reorder --last 10 --threshold 0.5 --budget 1500

  # same, plus apply any available loyalty bonuses to the cart
  silpo-agent reorder --last 10 --threshold 0.5 --optimize promos
"""


def main(
    argv: list[str] | None = None, *, client=None, log_store=None, input_fn=None, print_fn=None
) -> int:
    input_fn = input_fn or input
    print_fn = print_fn or print
    parser = argparse.ArgumentParser(
        prog="silpo-agent",
        description="Personal CLI wrapper over the Silpo MCP server -- rebuilds your grocery cart from what you "
        "usually buy, instead of re-typing the same list every week.",
        epilog=_TOP_LEVEL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('silpo-agent-cli')}"
    )
    subparsers = parser.add_subparsers(dest="command")
    reorder_parser = subparsers.add_parser(
        "reorder",
        help="Rebuild the cart from your typical items",
        description="Rebuild your Silpo cart from the products you buy most consistently, based on your real "
        "online order history. Fills the cart only -- checkout/payment is always a manual step.",
        epilog=_REORDER_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reorder_parser.add_argument(
        "--last",
        type=int,
        required=True,
        metavar="N",
        help="How many of your most recent online orders to consider. "
        "Errors out (without touching the cart) if you have fewer than N orders.",
    )
    reorder_parser.add_argument(
        "--threshold",
        type=float,
        required=True,
        metavar="0-1",
        help="Minimum share of the --last orders a product must appear in to count as a 'typical item' "
        "(e.g. 0.5 = bought in at least half of them).",
    )
    reorder_parser.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="UAH",
        help="Optional spend cap in UAH. If the typical-item total exceeds it, the least-frequently-bought "
        "items are trimmed first until it fits. Omit to add everything and just report the total.",
    )
    reorder_parser.add_argument(
        "--optimize",
        choices=["promos"],
        default=None,
        help="Opt-in only -- omitting this flag makes zero promo-related calls. "
        "'promos' applies any available loyalty bonuses to the cart before checkout.",
    )

    cart_parser = subparsers.add_parser(
        "cart",
        help="Show your current real cart (read-only); `cart edit`/`cart promos` for more",
        description="Show the current real Silpo cart: items (name/quantity/price/stock), the amount actually "
        "payable (totalAfterDiscounts, never the pre-discount total), any cart validations (stock/timeslot "
        "problems), and your loyalty bonus balance -- the default when no further subcommand is given. "
        "`cart edit` manually replaces one item with another; `cart promos` shows real promo alternatives "
        "for every item.",
    )
    cart_subparsers = cart_parser.add_subparsers(dest="cart_command")
    edit_parser = cart_subparsers.add_parser(
        "edit",
        help="Replace one cart item with another, add a brand-new one, or remove one outright",
        description="Replace one item in your real cart with another -- interactively (list current items, "
        "free-text search a replacement, confirm) or non-interactively via --replace for scripting. --add adds "
        "a new item without removing anything. --remove deletes a line without adding a replacement.",
        epilog=_CART_EDIT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    edit_group = edit_parser.add_mutually_exclusive_group()
    edit_group.add_argument(
        "--replace",
        nargs=2,
        metavar=("OLD_SLUG", "NEW_SLUG"),
        default=None,
        help="Non-interactive: replace OLD_SLUG with NEW_SLUG, zero prompts. Both are product slugs as printed "
        "by `cart`, `deals`, `favorites-deals` and `cart promos`.",
    )
    edit_group.add_argument(
        "--add",
        metavar="NEW_SLUG",
        default=None,
        help="Non-interactive: add NEW_SLUG as a new cart line (quantity 1, or --quantity), zero prompts, "
        "nothing removed. Errors instead of touching the cart if NEW_SLUG is already in it -- use --replace or "
        "reorder instead.",
    )
    edit_group.add_argument(
        "--qty",
        nargs=2,
        metavar=("SLUG", "NUM"),
        default=None,
        help="Non-interactive: set SLUG's existing cart line quantity to the absolute value NUM (a set, not a "
        "delta), zero prompts. NUM accepts a number, e.g. 0.5 for weighted products sold by kg. Errors instead "
        "of touching the cart if SLUG isn't already a line in it.",
    )
    edit_group.add_argument(
        "--remove",
        metavar="SLUG",
        default=None,
        help="Non-interactive: remove SLUG's cart line, zero prompts, nothing added back. Errors instead of "
        "touching the cart if SLUG isn't actually in it.",
    )
    edit_parser.add_argument(
        "--quantity",
        type=float,
        default=1,
        metavar="NUM",
        help="Quantity for --add (default: 1). Accepts a number, e.g. 0.5 for weighted products sold by kg. "
        "Only valid together with --add.",
    )
    cart_subparsers.add_parser(
        "promos",
        help="Show real promo alternatives for every item currently in your cart (read-only)",
        description="For each item currently in your cart, shows genuinely discounted similar products "
        "(Silpo's own similarity engine, not a name/category guess), ranked by discount size. Purely "
        "informational -- never swaps or modifies anything in your cart. An item with no discounted "
        "alternatives is reported as such, not as an error.",
    )

    subparsers.add_parser(
        "delivery",
        help="Explicitly set delivery address, delivery type, and timeslot",
        description="Explicitly set your delivery address, delivery type, and timeslot together, in one real "
        "update to your Silpo cart. Supports DeliveryHome, SelfPickup, and NovaPoshta.",
        epilog=_DELIVERY_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers.add_parser(
        "clear-context",
        help="Wipe your local reorder history and substitution memory",
        description="Wipe the local Reorder Log and Substitution Memory after confirmation. Purely local state -- "
        "no MCP calls, and the OS keyring auth token / real Silpo cart are never touched.",
        epilog=_CLEAR_CONTEXT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers.add_parser(
        "coupons",
        help="List your active loyalty coupons (read-only)",
        description="List your active loyalty coupons with their buying condition, validity dates, and reward. "
        "Read-only -- makes no changes to your cart or account, and never cross-references coupons against "
        "your cart or search results.",
    )

    subparsers.add_parser(
        "favorites-deals",
        help="List your favorited products currently on discount (read-only)",
        description="Check your own favorites list for items currently discounted (oldPrice > price). "
        "Read-only -- no matching/heuristic, since it's already your own explicit list.",
    )

    deals_parser = subparsers.add_parser(
        "deals",
        help="Show the best current store-wide discounts (read-only)",
        description="Scan active promotion categories store-wide and show the biggest current discounts, "
        "independent of your cart. Read-only -- makes no changes to your cart or account.",
    )
    deals_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        metavar="N",
        help="How many top deals to show, sorted by discount percentage descending. Default: 10.",
    )
    deals_parser.add_argument(
        "--category",
        default=None,
        metavar="NAME",
        help="Only show deals in this category, e.g. \"Овочі\". Matched by real category title "
        "(exact match preferred, else the shortest title containing it) -- errors clearly if nothing matches, "
        "rather than silently showing zero deals.",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "reorder":
        return _run_reorder(
            args.last,
            args.threshold,
            client or MCPClient(),
            log_store or ReorderLogStore(),
            budget=args.budget,
            optimize=args.optimize,
        )

    if args.command == "cart":
        if args.cart_command == "edit":
            if args.quantity != 1 and args.add is None:
                print_fn("cart edit: --quantity is only valid together with --add")
                return 1
            return _run_cart_edit(
                client or MCPClient(),
                log_store or ReorderLogStore(),
                input_fn,
                print_fn,
                args.replace,
                args.add,
                args.quantity,
                args.qty,
                args.remove,
            )
        if args.cart_command == "promos":
            return _run_cart_promos(client or MCPClient(), log_store or ReorderLogStore(), input_fn, print_fn)
        if args.cart_command is None:
            return _run_cart(client or MCPClient(), log_store or ReorderLogStore(), input_fn, print_fn)
        cart_parser.print_help()
        return 0

    if args.command == "delivery":
        return _run_delivery(client or MCPClient(), log_store or ReorderLogStore(), input_fn, print_fn)

    if args.command == "clear-context":
        return _run_clear_context(log_store or ReorderLogStore(), input_fn, print_fn)

    if args.command == "coupons":
        return _run_coupons(client or MCPClient())

    if args.command == "favorites-deals":
        return _run_favorites_deals(client or MCPClient(), log_store or ReorderLogStore(), input_fn, print_fn)

    if args.command == "deals":
        return _run_deals(
            client or MCPClient(), args.limit, log_store or ReorderLogStore(), input_fn, print_fn, args.category
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
