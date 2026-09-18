import pytest

from sourcing1688 import scoring
from sourcing1688.models import Offer, PriceTier, Supplier


def make_offer(**kwargs) -> Offer:
    defaults = dict(
        offer_id="1",
        title="纯棉短袖T恤",
        price_cny=30.0,
        moq=50,
        material_text="100%纯棉",
        supplier=Supplier(seller_id="s1", name="测试制衣厂", years_on_platform=5, is_trustpass=True,
                          repurchase_rate=0.35, response_rate=0.95, composite_rating=4.7),
    )
    defaults.update(kwargs)
    return Offer(**defaults)


# ------------------------------------------------------------- composition


@pytest.mark.parametrize(
    "composition, expected",
    [
        ("95%棉 5%氨纶", 95.0),
        ("100%纯棉 260g精梳棉", 100.0),
        ("100%聚酯纤维", 0.0),
        # Regression: a fixed-width lookahead attributed 氨纶 to the 棉 segment
        # and reported a 95% cotton tee as ~49% natural.
        ("80%棉 20%涤纶 400g加绒", 80.0),
        ("50%腈纶 50%粘胶纤维", 0.0),
        ("100%美利奴羊毛", 100.0),
    ],
)
def test_natural_fibre_parsing(composition, expected):
    assert scoring.natural_fiber_pct(make_offer(material_text=composition)) == expected


def test_unstated_composition_is_unknown_not_zero():
    # Scoring an unknown as 0% would eliminate every under-documented listing
    # for a reason that was never established.
    assert scoring.natural_fiber_pct(make_offer(material_text="", title="T恤")) is None


def test_gsm_extraction_ignores_implausible_numbers():
    assert scoring.gsm(make_offer(title="260g重磅纯棉T恤")) == 260
    assert scoring.gsm(make_offer(title="2件装T恤 2024新款")) is None


# ------------------------------------------------------------- hard filters


def test_moq_ceiling_eliminates(profile):
    profile["commercial"]["max_moq"] = 30
    reasons = scoring.hard_filter(make_offer(moq=500), profile)
    assert any("MOQ 500" in r for r in reasons)


def test_price_is_evaluated_at_the_order_quantity_not_the_headline(profile):
    profile["commercial"]["max_unit_price_cny"] = 25.0
    profile["commercial"]["ideal_order_quantity"] = 500
    profile["commercial"]["budget_total_cny"] = 0  # isolate the price rule
    offer = make_offer(price_cny=32.0, price_tiers=[PriceTier(50, 32.0), PriceTier(500, 22.0)])
    # Headline ¥32 breaches the ceiling; the tier unlocked at 500 units does not.
    assert scoring.hard_filter(offer, profile, quantity=500) == []


def test_counterfeit_language_is_disqualifying(profile):
    reasons = scoring.hard_filter(make_offer(title="外贸原单T恤 A货同款"), profile)
    assert any("ounterfeit" in r for r in reasons)


def test_banned_material_is_matched_through_the_lexicon(profile):
    profile["materials"]["banned"] = ["pu leather"]
    reasons = scoring.hard_filter(make_offer(title="PU皮短袖T恤", material_text="PU皮"), profile)
    assert any("pu leather" in r for r in reasons)


def test_natural_fibre_floor_blocks_synthetics(profile):
    profile["materials"]["min_natural_fiber_pct"] = 80
    reasons = scoring.hard_filter(make_offer(material_text="100%聚酯纤维"), profile)
    assert any("Natural fibre" in r for r in reasons)


def test_off_brief_garment_is_rejected(profile):
    # Regression: a keyword search for an embroidered cotton tee returned an
    # embroidered cotton *cap*, which scored well on every weighted factor.
    terms = scoring.brief_garment_terms("cotton t-shirt", profile)
    cap = make_offer(title="纯棉刺绣鸭舌帽 定制logo 棒球帽")
    tee = make_offer(title="纯棉短袖T恤")
    assert scoring.off_brief_reason(cap, terms) is not None
    assert scoring.off_brief_reason(tee, terms) is None


def test_budget_ceiling_considers_the_whole_order(profile):
    profile["commercial"]["budget_total_cny"] = 1000
    profile["commercial"]["ideal_order_quantity"] = 200
    reasons = scoring.hard_filter(make_offer(price_cny=30.0), profile, quantity=200)
    assert any("budget" in r for r in reasons)


# ------------------------------------------------------------------ scoring


def test_score_is_weighted_sum_of_its_factors(profile):
    scored = scoring.score_offer(make_offer(), profile)
    expected = sum(f.weight * f.raw_score for f in scored.factors)
    assert scored.score == pytest.approx(expected, abs=1e-4)
    assert 0.0 <= scored.score <= 1.0


def test_every_factor_carries_an_explanation(profile):
    scored = scoring.score_offer(make_offer(), profile)
    assert {f.name for f in scored.factors} == set(profile["weights"])
    assert all(f.detail for f in scored.factors), "a factor without a reason cannot be defended to the user"


def test_suspiciously_cheap_is_penalised_not_rewarded(profile):
    profile["commercial"].update({"target_unit_price_cny": 30.0, "min_unit_price_cny": 10.0})
    cheap = scoring.score_offer(make_offer(price_cny=3.0), profile, quantity=1)
    fair = scoring.score_offer(make_offer(price_cny=28.0), profile, quantity=1)
    cheap_price = next(f for f in cheap.factors if f.name == "price")
    fair_price = next(f for f in fair.factors if f.name == "price")
    assert cheap_price.raw_score < fair_price.raw_score


def test_rank_splits_passed_from_rejected(profile):
    profile["commercial"]["max_moq"] = 100
    offers = [make_offer(offer_id="ok", moq=50), make_offer(offer_id="bad", moq=5000)]
    passed, rejected = scoring.rank(offers, profile, brief="cotton t-shirt")
    assert [s.offer.offer_id for s in passed] == ["ok"]
    assert [s.offer.offer_id for s in rejected] == ["bad"]


def test_diagnosis_names_the_binding_constraint(profile):
    profile["commercial"].update({"max_moq": 10, "ideal_order_quantity": 100, "budget_total_cny": 0})
    offers = [make_offer(offer_id=str(i), moq=500) for i in range(3)]
    _passed, rejected = scoring.rank(offers, profile, brief="cotton t-shirt")
    diagnosis = scoring.diagnose_rejections(rejected, profile)
    assert diagnosis["blocking_constraints"][0]["setting"] == "commercial.max_moq"
    assert "500" in diagnosis["suggestions"][0]
