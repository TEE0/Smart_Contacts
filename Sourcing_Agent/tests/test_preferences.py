import pytest

from sourcing1688 import preferences


def test_defaults_are_complete_and_weights_normalised():
    profile = preferences.default_profile()
    assert set(profile) >= {"identity", "style", "fit", "materials", "commercial", "supplier", "logistics", "weights"}
    assert pytest.approx(sum(profile["weights"].values()), abs=1e-6) == 1.0


def test_patch_merges_lists_by_default_and_replaces_on_request():
    preferences.save_profile(preferences.default_profile())

    merged = preferences.update_profile({"materials": {"banned": ["acrylic"]}})
    assert "pu leather" in merged["materials"]["banned"]
    assert "acrylic" in merged["materials"]["banned"]

    replaced = preferences.update_profile({"materials": {"banned": ["nylon"]}}, replace_lists=True)
    assert replaced["materials"]["banned"] == ["nylon"]


def test_weights_are_renormalised_after_update():
    profile = preferences.update_profile({"weights": {"price": 10, "supplier_trust": 10}})
    assert pytest.approx(sum(profile["weights"].values()), abs=1e-6) == 1.0
    assert profile["weights"]["price"] > profile["weights"]["style_match"]


def test_ceiling_below_target_pulls_the_target_down():
    # A ceiling under the target is always a typo. Honouring the ceiling and
    # silently keeping the impossible target would filter out everything.
    profile = preferences.update_profile(
        {"commercial": {"target_unit_price_cny": 80, "max_unit_price_cny": 40}}
    )
    assert profile["commercial"]["target_unit_price_cny"] == 40


def test_rates_are_clamped_to_valid_ranges():
    profile = preferences.update_profile(
        {"supplier": {"min_repurchase_rate": 5.0, "min_composite_rating": 99, "min_years_on_platform": -3}}
    )
    assert profile["supplier"]["min_repurchase_rate"] == 1.0
    assert profile["supplier"]["min_composite_rating"] == 5.0
    assert profile["supplier"]["min_years_on_platform"] == 0.0


def test_unknown_sections_are_ignored():
    profile = preferences.update_profile({"not_a_section": {"x": 1}})
    assert "not_a_section" not in profile


def test_profile_round_trips_through_disk():
    preferences.update_profile({"notes": "prefers GOTS certified mills"})
    assert preferences.load_profile()["notes"] == "prefers GOTS certified mills"


def test_reset_restores_defaults():
    preferences.update_profile({"notes": "temporary"})
    assert preferences.reset_profile()["notes"] == ""
