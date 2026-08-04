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

from silpo_agent.address_resolver import resolve_address
from silpo_agent.auth import MCPClient
from silpo_agent.cart_context import resolve_cart_context
from silpo_agent.cart_writer import write_cart
from silpo_agent.coupons_lister import list_coupons
from silpo_agent.delivery_settings import run_delivery_settings
from silpo_agent.log_store import ReorderLogStore
from silpo_agent.order_aggregator import InsufficientOrderHistoryError, derive_typical_items
from silpo_agent.promo_optimizer import optimize_promos
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
    return 0


def _run_delivery(client, log_store, input_fn, print_fn) -> int:
    result = run_delivery_settings(client, log_store, input_fn=input_fn, print_fn=print_fn)
    return 0 if result.applied else 1


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


def _run_coupons(client) -> int:
    lines = list_coupons(client)
    if not lines:
        print("No active coupons found.")
        return 0
    for line in lines:
        print(line.format())
    return 0


_TOP_LEVEL_EPILOG = """\
First run triggers a one-time browser login (OAuth2.1+PKCE against
mcp.silpo.ua); the token is cached in your OS keyring afterward, so
later runs don't re-prompt until it expires.

commands:
  reorder         rebuild your cart from your typical (frequently-bought) items
  delivery        explicitly set your delivery address, delivery type, and timeslot
  clear-context   wipe your local reorder history and substitution memory
  coupons         list your active loyalty coupons (read-only)

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
     -- only DeliveryHome (regular home delivery) is supported end to end
     right now; picking anything else stops here without changing anything
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

    subparsers.add_parser(
        "delivery",
        help="Explicitly set delivery address, delivery type, and timeslot",
        description="Explicitly set your delivery address, delivery type, and timeslot together, in one real "
        "update to your Silpo cart. DeliveryHome only for now; other delivery types are a follow-up.",
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

    if args.command == "delivery":
        return _run_delivery(client or MCPClient(), log_store or ReorderLogStore(), input_fn, print_fn)

    if args.command == "clear-context":
        return _run_clear_context(log_store or ReorderLogStore(), input_fn, print_fn)

    if args.command == "coupons":
        return _run_coupons(client or MCPClient())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
