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

    cart_context = resolve_cart_context(client)

    orders = client.call("silpo_get_my_online_orders") or []
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


def main(argv: list[str] | None = None, *, client=None, log_store=None) -> int:
    parser = argparse.ArgumentParser(prog="silpo-agent", description="CLI wrapper over the Silpo MCP server")
    subparsers = parser.add_subparsers(dest="command")
    reorder_parser = subparsers.add_parser("reorder", help="Rebuild the cart from your typical items")
    reorder_parser.add_argument("--last", type=int, required=True, help="Number of past orders to consider")
    reorder_parser.add_argument(
        "--threshold", type=float, required=True, help="Minimum order-frequency share (0-1) to count as typical"
    )
    reorder_parser.add_argument(
        "--budget", type=float, default=None, help="Optional spend cap; trims lowest-priority items to fit"
    )
    reorder_parser.add_argument(
        "--optimize",
        choices=["promos"],
        default=None,
        help="Opt-in promo optimization: swap for cheaper promo equivalents and apply bonuses/promo codes",
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
