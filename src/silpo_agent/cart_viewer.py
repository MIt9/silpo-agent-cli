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


def format_cart(context: CartContext) -> list[str]:
    lines: list[str] = []

    if not context.products:
        lines.append("Your cart is empty.")
    else:
        lines.append(f"Cart ({len(context.products)} item(s)):")
        for product in context.products:
            name = product.get("name") or product.get("productId") or "?"
            quantity = product.get("quantity")
            price = product.get("price")
            stock = product.get("stock")
            lines.append(f"  - {name} x{quantity} @ {price} (stock: {stock})")

    if context.validations:
        lines.append("Validations:")
        for validation in context.validations:
            lines.append(f"  [{validation.get('level')}] {validation.get('message')}")

    if context.total_after_discounts is not None:
        lines.append(f"Payable total: {context.total_after_discounts:.2f}")

    if context.bonus_available is not None:
        lines.append(f"Bonus available: {context.bonus_available:.2f}")

    return lines
