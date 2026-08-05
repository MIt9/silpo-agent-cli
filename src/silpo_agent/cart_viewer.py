"""Cart Viewer: read-only `cart` subcommand (issue #27, part of #26). Formats
an already-resolved `CartContext` (see cart_context.py) into a human-readable
report -- no MCP calls of its own, no new schema assumptions.

Real schema (docs/mcp_schema.md's "Cart tools" section): each cart product
(`CartContext.products`, from `cart.shipments[0].products[]`) carries
`name`/`quantity`/`price`/`stock`. The amount actually payable is
`cart.calculation.totalAfterDiscounts`, never the pre-discount `total` --
`CartContext.total_after_discounts` carries this through (added for this
ticket; CartContext previously exposed no total at all).
"""

from silpo_agent.cart_context import CartContext


def _or_unknown(value):
    """"?" for a missing/None field instead of rendering the literal string
    "None" -- a real response with a gap in one field shouldn't make the
    whole line look broken."""
    return "?" if value is None else value


def format_cart(context: CartContext) -> list[str]:
    lines: list[str] = []

    # A cart with only zero-quantity line items (if the live API ever
    # returns that) is treated as non-empty here -- context.products is
    # non-empty, so it's listed with "x0" rather than folded into the
    # "empty cart" message. Not special-cased: an edge case that hasn't
    # been observed live, and "x0" is still an accurate, non-crashing render.
    if not context.products:
        lines.append("Your cart is empty.")
    else:
        lines.append(f"Cart ({len(context.products)} item(s)):")
        for product in context.products:
            name = product.get("name") or product.get("productId") or "?"
            quantity = _or_unknown(product.get("quantity"))
            price = _or_unknown(product.get("price"))
            stock = _or_unknown(product.get("stock"))
            # Issue #6: a weighted cart line's `quantity` is already the real
            # kg amount (docs/mcp_schema.md's order-history section confirms
            # fractional kg quantities like 1.232 for weighted goods) -- the
            # line's own `ratio` field (e.g. "100г") is unreliable for a
            # weighted item priced per kg, so it's ignored in favor of a
            # hardcoded "кг" unit. Driven by this cart line's own `weighted`
            # flag only, not a cross-call reconciliation with
            # resolve_product_by_slug's separate `ratio`.
            if product.get("weighted") and isinstance(quantity, (int, float)):
                qty_display = f"{float(quantity)} кг"
            else:
                qty_display = f"x{quantity}"
            line = f"  - {name} {qty_display} @ {price} (stock: {stock})"
            # Issue #50: the slug is this CLI's public product identifier --
            # `cart edit --replace` takes slugs, and this is the only command
            # that lists what's currently in the cart, so without it the
            # read/act loop can't be closed. Omitted entirely when absent:
            # a "?" would be an identifier that's guaranteed to fail.
            slug = product.get("slug")
            if slug:
                line += f"  {slug}"
            lines.append(line)

    if context.validations:
        lines.append("Validations:")
        for validation in context.validations:
            level = _or_unknown(validation.get("level"))
            message = _or_unknown(validation.get("message"))
            lines.append(f"  [{level}] {message}")

    if context.total_after_discounts is not None:
        lines.append(f"Payable total: {context.total_after_discounts:.2f}")

    if context.bonus_available is not None:
        lines.append(f"Bonus available: {context.bonus_available:.2f}")

    return lines
