"""silpo-agent CLI entrypoint. `reorder` (the Reorder flow) lands in a later
ticket; this wires the argument parser shape so it slots in without a rewrite.
"""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="silpo-agent", description="CLI wrapper over the Silpo MCP server")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("reorder", help="Rebuild the cart from your typical items")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "reorder":
        print("reorder: not yet implemented")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
