"""Weekly grocery norm dataset: per-person quantities by basket type
(basic/eco/premium), ported from the household-basket research (see
.scratch/smart-cart/issues/01-norm-dataset.md). Pure data, no network calls
-- same shape as order_aggregator.py's TypicalItem/derive_typical_items.

Ticket 06: a few items additionally carry an optional seasonal profile
(`in_season_months` + `out_of_season_multiplier`, ported loosely from the
research's `SeasonalProfile`). `get_norms` takes an optional `month`
(1-12, caller-supplied -- this module never calls `datetime.now()` itself,
see the ticket) and scales `quantity_per_person` for any seasonal item that's
out of season that month. Items without a profile (most of the dataset) are
completely unaffected by `month` at any value.
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class NormItem:
    category: str
    name_ua: str
    quantity_per_person: float
    unit: str
    in_season_months: tuple[int, ...] | None = None
    out_of_season_multiplier: float | None = None

    def __post_init__(self) -> None:
        # Both-or-neither: a half-filled profile would otherwise only fail
        # later, deep inside get_norms's multiplication, the first time
        # `month` happens to be passed for this item.
        has_months = self.in_season_months is not None
        has_multiplier = self.out_of_season_multiplier is not None
        if has_months != has_multiplier:
            raise ValueError(
                "NormItem's seasonal profile needs both in_season_months and "
                "out_of_season_multiplier set together, or neither"
            )


# Ukrainian apples are in season roughly August-December (local harvest);
# outside that window they're imported/cold-stored, hence the price bump.
_APPLE_SEASON = (8, 9, 10, 11, 12)
_APPLE_OUT_OF_SEASON_MULTIPLIER = 1.2

# Berries (premium-only item): local season is short, June-July -- well
# outside that window they're imported and notably pricier.
_BERRY_SEASON = (6, 7)
_BERRY_OUT_OF_SEASON_MULTIPLIER = 1.8

_NORMS: dict[str, list[NormItem]] = {
    "basic": [
        NormItem("vegetables", "Картопля", 0.5, "kg"),
        NormItem("fruits", "Яблука", 0.4, "kg", _APPLE_SEASON, _APPLE_OUT_OF_SEASON_MULTIPLIER),
        NormItem("protein", "Куряче філе", 0.25, "kg"),
        NormItem("dairy", "Молоко", 0.5, "l"),
        NormItem("grains", "Гречка", 0.1, "kg"),
        NormItem("pantry", "Олія соняшникова", 0.03, "l"),
        NormItem("coffee", "Кава розчинна", 0.015, "kg"),
        NormItem("tea", "Чай чорний", 0.01, "kg"),
    ],
    "eco": [
        NormItem("vegetables", "Картопля", 0.5, "kg"),
        NormItem("fruits", "Яблука", 0.5, "kg", _APPLE_SEASON, _APPLE_OUT_OF_SEASON_MULTIPLIER),
        NormItem("protein", "Куряче філе", 0.3, "kg"),
        NormItem("dairy", "Молоко", 0.6, "l"),
        NormItem("grains", "Гречка", 0.12, "kg"),
        NormItem("pantry", "Олія соняшникова", 0.03, "l"),
        NormItem("coffee", "Кава мелена", 0.02, "kg"),
        NormItem("tea", "Чай чорний", 0.012, "kg"),
    ],
    "premium": [
        NormItem("vegetables", "Картопля", 0.5, "kg"),
        NormItem("fruits", "Яблука", 0.6, "kg", _APPLE_SEASON, _APPLE_OUT_OF_SEASON_MULTIPLIER),
        NormItem("fruits", "Сезонні ягоди", 0.3, "kg", _BERRY_SEASON, _BERRY_OUT_OF_SEASON_MULTIPLIER),
        NormItem("protein", "Куряче філе", 0.4, "kg"),
        NormItem("dairy", "Молоко", 0.7, "l"),
        NormItem("grains", "Гречка", 0.15, "kg"),
        NormItem("pantry", "Олія соняшникова", 0.03, "l"),
        NormItem("coffee", "Кава в зернах", 0.03, "kg"),
        NormItem("tea", "Чай зелений", 0.015, "kg"),
    ],
}


def get_norms(basket_type: str, month: int | None = None) -> list[NormItem]:
    if basket_type not in _NORMS:
        raise ValueError(f"unknown basket_type {basket_type!r}, expected one of {sorted(_NORMS)}")
    items = _NORMS[basket_type]
    if month is None:
        return items
    return [
        replace(item, quantity_per_person=item.quantity_per_person * item.out_of_season_multiplier)
        if item.in_season_months is not None and month not in item.in_season_months
        else item
        for item in items
    ]
