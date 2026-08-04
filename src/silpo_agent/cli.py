"""silpo-agent CLI entrypoint. `reorder` wires the Order Aggregator and Cart
Writer together for the happy path (issue #3): fetch orders, derive typical
items, silently use the default delivery address, fill the cart, report.

Address confirmation UX, substitution handling, promo optimization, and
budget cap are separate tickets (#4, #5, #6) — not built here.
"""

import argparse
import sys

from silpo_agent.auth import MCPClient
from silpo_agent.cart_writer import write_cart
from silpo_agent.order_aggregator import InsufficientOrderHistoryError, derive_typical_items


def _resolve_default_address(client) -> str | None:
    """Silent default-address use only (no confirmation UX — issue #4):
    picks the MCP-marked default, falling back to the first saved address.
    Schema of each address entry is unverified live — see docs/mcp_schema.md.
    """
    addresses = client.call("silpo_get_my_delivery_addresses") or []
    if not addresses:
        return None
    default = next((a for a in addresses if a.get("is_default")), addresses[0])
    return default.get("address") or default.get("id")


def _run_reorder(last: int, threshold: float, client) -> int:
    orders = client.call("silpo_get_my_online_orders") or []
    try:
        typical_items = derive_typical_items(orders, last=last, threshold=threshold)
    except InsufficientOrderHistoryError as exc:
        print(f"reorder: {exc}", file=sys.stderr)
        return 1

    address = _resolve_default_address(client)
    report = write_cart(client, typical_items)

    if address:
        print(f"Delivering to: {address}")
    print(f"Added {len(report.items_added)} item(s):")
    for product_id, price in report.items_added:
        print(f"  - {product_id}: {price:.2f}")
    print(f"Total: {report.total:.2f}")
    return 0


def main(argv: list[str] | None = None, *, client=None) -> int:
    parser = argparse.ArgumentParser(prog="silpo-agent", description="CLI wrapper over the Silpo MCP server")
    subparsers = parser.add_subparsers(dest="command")
    reorder_parser = subparsers.add_parser("reorder", help="Rebuild the cart from your typical items")
    reorder_parser.add_argument("--last", type=int, required=True, help="Number of past orders to consider")
    reorder_parser.add_argument(
        "--threshold", type=float, required=True, help="Minimum order-frequency share (0-1) to count as typical"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "reorder":
        return _run_reorder(args.last, args.threshold, client or MCPClient())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
