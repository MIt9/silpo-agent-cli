from silpo_agent.cart_context import CartContext
from silpo_agent.cart_viewer import format_cart


def _context(**overrides):
    defaults = dict(
        shopping_cart_id="cart-1",
        branch_id="b1",
        company_id="c1",
        delivery_type="DeliveryHome",
        timeslot_start="2026-08-04T10:00:00",
        timeslot_end="2026-08-04T12:00:00",
    )
    defaults.update(overrides)
    return CartContext(**defaults)


def test_empty_cart_renders_sensibly_not_crash():
    context = _context(products=[])

    lines = format_cart(context)

    assert any("empty" in line.lower() for line in lines)
    assert not any("Payable total" in line for line in lines)


def test_populated_cart_shows_items_payable_total_and_bonus():
    products = [
        {"productId": "p1", "name": "Milk 1L", "quantity": 2, "price": 49.9, "stock": 12},
        {"productId": "p2", "name": "Bread", "quantity": 1, "price": 30.0, "stock": 0},
    ]
    context = _context(products=products, total_after_discounts=166.7, bonus_available=24.27)

    lines = format_cart(context)
    text = "\n".join(lines)

    assert "Milk 1L" in text and "x2" in text and "49.9" in text and "stock: 12" in text
    assert "Bread" in text and "stock: 0" in text
    assert "Payable total: 166.70" in text
    assert "Bonus available: 24.27" in text
    assert "empty" not in text.lower()


def test_cart_lines_carry_the_slug_so_cart_edit_can_act_on_them(capsys):
    """Issue #50: `cart edit --replace` takes slugs, so `cart` -- the only
    source of an item's identifier -- must print it, or the read/act loop
    can't be closed without dropping to raw MCP calls."""
    products = [
        {
            "productId": "1ed0762e-b3f9-6ca8-bdbf-dd63763181f9",
            "slug": "moloko-ferma-ultrapasteryzovane-2-5-576829",
            "name": "Молоко «Ферма» ультрапастеризоване 2,5%",
            "quantity": 2,
            "price": 49.9,
            "stock": 189,
        }
    ]
    context = _context(products=products)

    text = "\n".join(format_cart(context))

    assert "moloko-ferma-ultrapasteryzovane-2-5-576829" in text


def test_product_without_a_slug_renders_without_a_dangling_identifier():
    """Not every product record is guaranteed to carry a slug. A missing one
    is omitted entirely rather than rendered as "?" -- an unusable
    identifier is worse than none, it invites a failing `--replace`."""
    products = [{"productId": "p1", "name": "Milk 1L", "quantity": 1, "price": 49.9, "stock": 3}]
    context = _context(products=products)

    text = "\n".join(format_cart(context))

    assert "None" not in text
    assert text.rstrip().endswith("(stock: 3)")


def test_cart_with_validations_shows_them_without_blocking():
    validations = [
        {"level": "error", "type": "timeslot", "message": "timeslot.not_found", "context": []},
        {"level": "error", "type": "product", "message": "product.offer.stock.max",
         "context": {"productId": "p1", "stock": 0}},
    ]
    products = [{"productId": "p1", "name": "Milk 1L", "quantity": 1, "price": 49.9, "stock": 0}]
    context = _context(products=products, validations=validations, total_after_discounts=49.9)

    lines = format_cart(context)
    text = "\n".join(lines)

    assert "timeslot.not_found" in text
    assert "product.offer.stock.max" in text
    # Non-blocking: items and payable total still render alongside the validations.
    assert "Milk 1L" in text
    assert "Payable total: 49.90" in text


def test_missing_product_and_validation_fields_render_as_unknown_not_literal_none():
    """A real response with a gap in one field (missing quantity/price/stock
    on a product, or missing level/message on a validation) must not render
    the Python literal "None" -- fall back to "?" instead."""
    products = [{"productId": "p1", "name": "Milk 1L"}]  # no quantity/price/stock
    validations = [{"type": "timeslot", "context": []}]  # no level/message
    context = _context(products=products, validations=validations)

    lines = format_cart(context)
    text = "\n".join(lines)

    assert "None" not in text
    assert "x? @ ? (stock: ?)" in text
    assert "[?] ?" in text
