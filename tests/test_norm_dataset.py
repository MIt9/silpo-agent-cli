import pytest

from silpo_agent.norm_dataset import NormItem, get_norms


def test_eco_covers_all_eight_categories():
    result = get_norms("eco")

    categories = {item.category for item in result}
    assert categories == {
        "vegetables",
        "fruits",
        "protein",
        "dairy",
        "grains",
        "pantry",
        "coffee",
        "tea",
    }


def _by_name(items: list[NormItem], name_ua: str) -> NormItem:
    return next(item for item in items if item.name_ua == name_ua)


def test_basic_and_premium_differ_from_eco_for_same_product():
    basic_chicken = _by_name(get_norms("basic"), "Куряче філе")
    eco_chicken = _by_name(get_norms("eco"), "Куряче філе")
    premium_chicken = _by_name(get_norms("premium"), "Куряче філе")

    assert basic_chicken.quantity_per_person < eco_chicken.quantity_per_person < premium_chicken.quantity_per_person


def test_premium_only_product_absent_from_basic():
    basic_names = {item.name_ua for item in get_norms("basic")}
    premium_names = {item.name_ua for item in get_norms("premium")}

    assert "Сезонні ягоди" in premium_names
    assert "Сезонні ягоди" not in basic_names


def test_invalid_basket_type_raises():
    with pytest.raises(ValueError, match="luxury"):
        get_norms("luxury")


# --- ticket 06: seasonal profile -----------------------------------------


def test_no_profile_item_unaffected_by_month_argument():
    potato_no_month = _by_name(get_norms("eco"), "Картопля")
    potato_january = _by_name(get_norms("eco", month=1), "Картопля")
    potato_july = _by_name(get_norms("eco", month=7), "Картопля")

    assert potato_no_month.quantity_per_person == potato_january.quantity_per_person == potato_july.quantity_per_person


def test_seasonal_item_scaled_when_out_of_season():
    apple_in_season = _by_name(get_norms("eco", month=9), "Яблука")  # Sep is in-season
    apple_out_of_season = _by_name(get_norms("eco", month=3), "Яблука")  # March is not

    base = _by_name(get_norms("eco"), "Яблука")
    assert apple_in_season.quantity_per_person == base.quantity_per_person
    assert apple_out_of_season.quantity_per_person == pytest.approx(
        base.quantity_per_person * apple_out_of_season.out_of_season_multiplier
    )
    assert apple_out_of_season.quantity_per_person > apple_in_season.quantity_per_person


def test_seasonal_item_unaffected_when_month_omitted():
    scaled_default = _by_name(get_norms("eco"), "Яблука")
    scaled_no_month = _by_name(get_norms("eco", month=None), "Яблука")

    assert scaled_default.quantity_per_person == scaled_no_month.quantity_per_person


def test_premium_only_seasonal_item_scaled_when_out_of_season():
    berries_in_season = _by_name(get_norms("premium", month=6), "Сезонні ягоди")
    berries_out_of_season = _by_name(get_norms("premium", month=12), "Сезонні ягоди")

    assert berries_out_of_season.quantity_per_person > berries_in_season.quantity_per_person


def test_half_filled_seasonal_profile_rejected_at_construction():
    """in_season_months and out_of_season_multiplier must be set together --
    a half-filled profile would otherwise only blow up later, deep inside
    get_norms's multiplication, the first time `month` is passed."""
    with pytest.raises(ValueError, match="in_season_months"):
        NormItem("fruits", "Груші", 0.4, "kg", in_season_months=(9, 10))

    with pytest.raises(ValueError, match="out_of_season_multiplier"):
        NormItem("fruits", "Груші", 0.4, "kg", out_of_season_multiplier=1.3)
