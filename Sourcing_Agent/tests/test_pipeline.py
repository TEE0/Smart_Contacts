from sourcing1688 import preferences
from sourcing1688.pipeline import compare_offers, source_clothing
from sourcing1688.planner import explain_plan, plan_queries


# ------------------------------------------------------------------ planner


def test_plan_fans_out_across_intents(profile):
    plans = plan_queries("heavyweight oversized cotton tee with embroidered logo", profile)
    intents = {p.intent for p in plans}
    assert "primary" in intents and "fallback" in intents
    assert all(p.query_zh for p in plans), "every query must be in Chinese"
    assert len({p.query_zh for p in plans}) == len(plans), "queries must be distinct"


def test_garment_survives_query_truncation(profile):
    # Regression: a term-count cap dropped the garment noun and left a query of
    # pure adjectives, which matches every category on 1688 at once.
    plans = plan_queries("heavyweight oversized cotton tee, embroidered logo, bouclé trim", profile)
    assert "T恤" in plans[0].query_zh


def test_conflicting_fit_preferences_do_not_both_enter_the_query(profile):
    profile["fit"]["preferred_fits"] = ["oversized", "slim fit"]
    primary = plan_queries("cotton tee", profile)[0].query_zh
    assert not ("宽松" in primary and "修身" in primary)


def test_plan_carries_the_profile_price_band(profile):
    profile["commercial"].update({"min_unit_price_cny": 10, "max_unit_price_cny": 40})
    plan = plan_queries("cotton tee", profile)[0]
    assert plan.filters["price_start"] == 10
    # The upstream ceiling is widened because tier pricing lands below it.
    assert plan.filters["price_end"] > 40


def test_explain_plan_warns_about_dropped_words(profile):
    explained = explain_plan("cotton tee with lurex piping", profile, plan_queries("cotton tee with lurex piping", profile))
    assert "lurex" in explained["unrecognized_terms"]
    assert "lurex" in explained["translation_warning"]


def test_brief_without_a_garment_falls_back_to_profile_categories(profile):
    profile["style"]["categories"] = ["hoodie"]
    plans = plan_queries("something warm for winter", profile)
    assert any("卫衣" in p.query_zh for p in plans)


# ----------------------------------------------------------------- pipeline


def test_end_to_end_run_produces_a_ranked_costed_shortlist():
    preferences.update_profile({"commercial": {"budget_total_cny": 50000, "max_unit_price_cny": 80}})
    result = source_clothing("heavyweight cotton tee with embroidered logo", top_n=3)

    assert result["status"] == "ok"
    assert result["shortlist"], "the fixture catalogue contains matching tees"
    scores = [entry["score"] for entry in result["shortlist"]]
    assert all(0 <= s <= 1 for s in scores)
    for entry in result["shortlist"]:
        assert entry["landed_cost"]["landed_unit_cost"] > 0
        assert entry["factors"], "a placement with no factors cannot be explained"


def test_shortlist_contains_only_the_requested_garment_class():
    preferences.update_profile({"commercial": {"budget_total_cny": 50000, "max_unit_price_cny": 80}})
    result = source_clothing("cotton t-shirt", top_n=5)
    assert result["shortlist"]
    for entry in result["shortlist"]:
        assert "T恤" in entry["offer"]["title"]


def test_hard_constraints_are_actually_enforced():
    preferences.update_profile({"commercial": {"max_moq": 25, "budget_total_cny": 100000}})
    result = source_clothing("cotton t-shirt", top_n=5)
    assert all(entry["offer"]["moq"] <= 25 for entry in result["shortlist"])


def test_empty_shortlist_names_the_binding_constraint():
    preferences.update_profile({"commercial": {"max_unit_price_cny": 1.0, "budget_total_cny": 100000}})
    result = source_clothing("cotton t-shirt", top_n=5)
    assert result["shortlist"] == []
    assert result["diagnosis"]["blocking_constraints"], "an empty result must explain itself"
    assert any("ceiling" in s for s in result["next_steps"])


def test_rejected_sample_puts_on_brief_listings_first():
    preferences.update_profile({"commercial": {"max_moq": 1, "budget_total_cny": 100000}})
    result = source_clothing("cotton t-shirt", top_n=3)
    reasons = result["rejected_sample"][0]["reasons"]
    assert not any("Not the garment" in r for r in reasons)


def test_quantity_override_changes_the_costing_basis():
    preferences.update_profile({"commercial": {"budget_total_cny": 500000, "max_unit_price_cny": 80}})
    small = source_clothing("cotton t-shirt", top_n=1, quantity=50)
    large = source_clothing("cotton t-shirt", top_n=1, quantity=1000)
    assert small["shortlist"][0]["landed_cost"]["quantity"] == 50
    assert large["shortlist"][0]["landed_cost"]["quantity"] == 1000


def test_plan_is_always_returned_so_the_search_can_be_audited():
    result = source_clothing("cotton t-shirt", top_n=1)
    assert result["plan"]["queries"]
    assert "recognized_terms" in result["plan"]


def test_compare_offers_reports_constraints_without_applying_them():
    preferences.update_profile({"commercial": {"max_moq": 1}})
    result = compare_offers(["610001", "610002"])
    assert result["status"] == "ok"
    assert len(result["comparison"]) == 2, "explicitly requested listings are never hidden"
    assert any(row["rejected_reasons"] for row in result["comparison"])


def test_compare_offers_reports_missing_ids():
    result = compare_offers(["610001", "000000"])
    assert any("000000" in m for m in result["missing"])
