import pytest

from sourcing1688 import costing
from sourcing1688.models import Offer, PriceTier


def tee(price=30.0, moq=50, tiers=None) -> Offer:
    return Offer(offer_id="t", title="纯棉短袖T恤", price_cny=price, moq=moq, price_tiers=tiers or [])


def jacket() -> Offer:
    return Offer(offer_id="j", title="冬季白鸭绒羽绒服", price_cny=168.0, moq=30)


def test_weight_is_inferred_from_garment_type():
    tee_kg, tee_src = costing.estimate_unit_weight(tee())
    jacket_kg, _ = costing.estimate_unit_weight(jacket())
    assert tee_kg < jacket_kg, "a down jacket must not be costed as a t-shirt"
    assert "inferred" in tee_src


def test_published_weight_wins_over_inference():
    offer = tee()
    offer.unit_weight_kg = 0.42
    kg, source = costing.estimate_unit_weight(offer)
    assert kg == 0.42 and "published" in source


def test_landed_cost_itemises_every_component(profile):
    result = costing.landed_cost(tee(), profile, quantity=200)
    assert set(result["breakdown_order_total"]) == {
        "goods", "domestic_freight_cn", "agent_fee", "international_freight", "duty", "import_tax", "qc_inspection",
    }
    assert result["breakdown_order_total"].keys() == result["breakdown_per_unit"].keys()
    total = sum(result["breakdown_order_total"].values())
    assert result["landed_total"] == pytest.approx(total, rel=0.01)


def test_per_unit_breakdown_matches_the_headline(profile):
    result = costing.landed_cost(tee(), profile, quantity=200)
    assert sum(result["breakdown_per_unit"].values()) == pytest.approx(result["landed_unit_cost"], rel=0.01)


def test_landed_cost_exceeds_ex_works(profile):
    result = costing.landed_cost(tee(price=30.0), profile, quantity=200)
    ex_works = 30.0 / result["assumptions"]["fx_cny_per_unit"]
    assert result["landed_unit_cost"] > ex_works
    assert result["uplift_vs_ex_works_pct"] > 0


def test_sea_is_cheaper_than_air_which_is_cheaper_than_express(profile):
    costs = {
        mode: costing.landed_cost(tee(), profile, quantity=300, shipping_mode=mode)["landed_unit_cost"]
        for mode in ("sea", "air", "express")
    }
    assert costs["sea"] < costs["air"] < costs["express"]


def test_quantity_unlocks_the_price_ladder(profile):
    offer = tee(price=32.0, moq=50, tiers=[PriceTier(50, 32.0), PriceTier(500, 22.0)])
    small = costing.landed_cost(offer, profile, quantity=50)
    large = costing.landed_cost(offer, profile, quantity=500)
    assert large["unit_price_cny"] == 22.0
    assert large["landed_unit_cost"] < small["landed_unit_cost"]


def test_quantity_is_raised_to_the_moq(profile):
    # Costing 10 units of a 50-MOQ listing would quote a price the buyer
    # cannot actually place.
    result = costing.landed_cost(tee(moq=50), profile, quantity=10)
    assert result["quantity"] == 50


def test_assumptions_are_always_returned(profile):
    assumptions = costing.landed_cost(tee(), profile, quantity=100)["assumptions"]
    assert {"fx_cny_per_unit", "unit_weight_kg", "freight_rate_per_kg", "duty_pct", "import_tax_pct"} <= set(assumptions)
    assert "not quotes" in assumptions["note"]


def test_overrides_take_precedence(profile):
    result = costing.landed_cost(tee(), profile, quantity=100, overrides={"duty_pct": 0, "import_tax_pct": 0})
    assert result["breakdown_order_total"]["duty"] == 0
    assert result["breakdown_order_total"]["import_tax"] == 0


def test_comparison_is_sorted_by_landed_not_headline_price(profile):
    # The whole point: a lighter garment can land cheaper than one with a
    # lower sticker price.
    heavy_cheap = Offer(offer_id="heavy", title="冬季白鸭绒羽绒服", price_cny=100.0, moq=1)
    light_dear = Offer(offer_id="light", title="纯棉短袖T恤", price_cny=110.0, moq=1)
    rows = costing.compare_landed([heavy_cheap, light_dear], profile, quantity=200, shipping_mode="air")
    assert [r["landed_unit_cost"] for r in rows] == sorted(r["landed_unit_cost"] for r in rows)
