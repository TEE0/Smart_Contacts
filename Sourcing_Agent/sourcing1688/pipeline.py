"""End-to-end sourcing run: brief in, ranked and costed shortlist out.

Order of operations matters and is deliberate:

1. **Plan** queries from the brief plus the standing profile.
2. **Fan out** across those queries and dedupe, so the shortlist reflects
   distinct suppliers rather than one listing found five ways.
3. **Hard filter** before spending anything on detail calls - eliminating on
   MOQ and banned fabric is free, fetching detail is not.
4. **Enrich** only the survivors that could plausibly make the shortlist.
5. **Score**, **vet** and **cost** the enriched set, then re-rank on the
   combination.

Step 5 re-ranks rather than trusting step 3's order because detail calls change
the answer: price ladders, real MOQs and composition strings routinely move an
offer several places.
"""

from __future__ import annotations

from typing import Any

from . import costing, scoring
from .models import Offer, ScoredOffer
from .planner import explain_plan, plan_queries
from .preferences import load_profile
from .providers import ProviderError, dedupe_offers, get_provider
from .providers.base import SourcingProvider


def _enrich(provider: SourcingProvider, offers: list[Offer], limit: int) -> tuple[list[Offer], list[str]]:
    """Fetch full detail for the top candidates, tolerating per-offer failures."""
    enriched: list[Offer] = []
    warnings: list[str] = []
    for offer in offers[:limit]:
        try:
            detailed = provider.get_offer(offer.offer_id)
        except ProviderError as exc:
            warnings.append(f"Detail lookup failed for {offer.offer_id}: {exc}")
            enriched.append(offer)
            continue
        if detailed:
            detailed.source_query = offer.source_query or detailed.source_query
            enriched.append(detailed)
        else:
            enriched.append(offer)
    enriched.extend(offers[limit:])
    return enriched, warnings


def source_clothing(
    brief: str,
    *,
    profile: dict[str, Any] | None = None,
    provider_name: str | None = None,
    top_n: int = 5,
    quantity: int | None = None,
    max_queries: int = 6,
    include_rejected: int = 5,
    enrich: bool = True,
) -> dict[str, Any]:
    """Run the whole selection workflow for one brief."""
    profile = profile or load_profile()
    provider = get_provider(provider_name)
    plans = plan_queries(brief, profile, max_queries=max_queries)

    warnings: list[str] = []
    batches: list[list[Offer]] = []
    for plan in plans:
        try:
            batches.append(provider.search(plan.query_zh, limit=40, filters=plan.filters))
        except ProviderError as exc:
            warnings.append(f"Query {plan.query_zh!r} failed: {exc}")

    offers = dedupe_offers(batches)
    if not offers:
        return _no_results(brief, profile, provider, plans, warnings)

    quantity = quantity or int(profile.get("commercial", {}).get("ideal_order_quantity") or 1)

    # Cheap filtering first, detail calls only for plausible survivors.
    pre_passed, pre_rejected = scoring.rank(offers, profile, brief=brief, quantity=quantity)

    if enrich and pre_passed:
        from .config import get_settings

        limit = min(get_settings().detail_fetch_limit, max(top_n * 2, 6))
        enriched, enrich_warnings = _enrich(provider, [s.offer for s in pre_passed], limit)
        warnings.extend(enrich_warnings)
        passed, rejected_after = scoring.rank(enriched, profile, brief=brief, quantity=quantity)
        # An offer eliminated only after enrichment is worth reporting: the
        # detail call is what revealed the real MOQ or composition.
        rejected = pre_rejected + rejected_after
    else:
        passed, rejected = pre_passed, pre_rejected

    shortlist = passed[:top_n]
    for scored in shortlist:
        scored.landed_cost = costing.landed_cost(scored.offer, profile, quantity=quantity)

    # Re-rank the shortlist on landed cost where scores are close: a 2% score
    # gap does not justify a 20% landed-cost gap.
    shortlist = _rerank_on_landed_cost(shortlist)

    return {
        "brief": brief,
        "status": "ok",
        "provider": getattr(provider, "name", "unknown"),
        "quantity_assumed": quantity,
        "plan": explain_plan(brief, profile, plans),
        "candidates_found": len(offers),
        "candidates_passed": len(passed),
        "candidates_rejected": len(rejected),
        "shortlist": [s.as_dict() for s in shortlist],
        # On-brief rejections first: "the right product, ruled out by your rules"
        # is actionable, "wrong product" is just search noise.
        "rejected_sample": [
            {
                "offer_id": s.offer.offer_id,
                "title": s.offer.title,
                "price_cny": s.offer.price_cny,
                "reasons": s.rejected_reasons,
            }
            for s in sorted(
                rejected,
                key=lambda s: any(scoring.classify_reason(r) == "off_brief" for r in s.rejected_reasons),
            )[:include_rejected]
        ],
        "landed_cost_comparison": costing.compare_landed([s.offer for s in shortlist], profile, quantity=quantity),
        "warnings": warnings,
        "diagnosis": scoring.diagnose_rejections(rejected, profile) if rejected else {},
        "next_steps": _next_steps(shortlist, profile, rejected),
    }


def _no_results(brief, profile, provider, plans, warnings) -> dict[str, Any]:
    """Explain an empty search rather than reporting "nothing found".

    An empty result has two very different causes: the price band sent upstream
    excluded the whole market, or the market genuinely has nothing. Those need
    opposite responses, so the difference is established by re-probing the
    primary query with the price filter removed - one extra call, and it turns
    a dead end into an actionable answer.
    """
    probe_offers: list[Offer] = []
    filtered_upstream = any(p.filters for p in plans)
    if plans and filtered_upstream:
        try:
            probe_offers = provider.search(plans[0].query_zh, limit=20, filters={})
        except ProviderError as exc:
            warnings.append(f"Unfiltered probe failed: {exc}")

    if probe_offers:
        prices = sorted(o.price_cny for o in probe_offers if o.price_cny)
        band = f"¥{prices[0]:.2f} - ¥{prices[-1]:.2f}" if prices else "unknown"
        commercial = profile.get("commercial", {})
        diagnosis = {
            "cause": "price_band_too_narrow",
            "detail": (
                f"The same query returns {len(probe_offers)} listing(s) without your price filter. "
                f"The market band for this search is {band}, against your "
                f"¥{float(commercial.get('min_unit_price_cny') or 0):.2f}-"
                f"¥{float(commercial.get('max_unit_price_cny') or 0):.2f} range."
            ),
            "observed_market_band_cny": {"low": prices[0] if prices else None, "high": prices[-1] if prices else None},
            "blocking_constraints": [{"setting": "commercial.max_unit_price_cny", "listings_blocked": len(probe_offers)}],
            "off_brief_count": 0,
            "on_brief_blocked_count": len(probe_offers),
        }
        next_steps = [
            "Raise your unit-price ceiling (commercial.max_unit_price_cny) or lower the floor (min_unit_price_cny) so the range overlaps the market band above.",
            "Or increase the planned quantity - tier pricing may bring the unit price into your range.",
        ]
    else:
        diagnosis = {
            "cause": "no_matching_listings",
            "detail": "No listings were returned even without your price filter, so the queries themselves are missing the market.",
            "blocking_constraints": [],
            "off_brief_count": 0,
            "on_brief_blocked_count": 0,
        }
        next_steps = [
            "Give me the Chinese term for anything the plan listed as unrecognised.",
            "Or broaden the brief - the fallback query may be too specific for this category.",
        ]

    return {
        "brief": brief,
        "status": "no_results",
        "provider": getattr(provider, "name", "unknown"),
        "plan": explain_plan(brief, profile, plans),
        "warnings": warnings,
        "shortlist": [],
        "diagnosis": {**diagnosis, "suggestions": next_steps},
        "next_steps": next_steps,
    }


def _rerank_on_landed_cost(shortlist: list[ScoredOffer]) -> list[ScoredOffer]:
    """Promote a materially cheaper landed cost over a marginally higher score."""
    if len(shortlist) < 2:
        return shortlist

    def key(entry: ScoredOffer) -> tuple[float, float]:
        landed = (entry.landed_cost or {}).get("landed_unit_cost") or 0.0
        # Bucket scores into 5-point bands so only meaningful score gaps outrank cost.
        band = round(entry.score * 20)
        return (-band, landed)

    return sorted(shortlist, key=key)


def _next_steps(shortlist: list[ScoredOffer], profile: dict[str, Any], rejected: list[ScoredOffer] | None = None) -> list[str]:
    if not shortlist:
        diagnosis = scoring.diagnose_rejections(rejected or [], profile)
        steps = ["Nothing cleared your hard constraints."]
        steps.extend(diagnosis.get("suggestions") or [])
        steps.append(diagnosis.get("interpretation", ""))
        return [s for s in steps if s]

    steps = [
        f"Request samples from the top {min(2, len(shortlist))} before committing "
        f"(your profile says {'samples are required' if profile.get('commercial', {}).get('needs_sample_first') else 'samples are optional'}).",
        "Ask each supplier for: fabric composition certificate, actual gsm, carton dimensions and gross weight, "
        "and a production lead time for your quantity.",
        "Confirm the price ladder in writing - the tier shown on the listing is an invitation, not a quote.",
    ]
    risky = [s for s in shortlist if s.risk_flags]
    if risky:
        steps.append(
            f"{len(risky)} of the shortlist carry risk flags - read them before paying a deposit."
        )
    return steps


def compare_offers(
    offer_ids: list[str],
    *,
    profile: dict[str, Any] | None = None,
    provider_name: str | None = None,
    quantity: int | None = None,
) -> dict[str, Any]:
    """Head-to-head comparison of specific listings the buyer already found."""
    profile = profile or load_profile()
    provider = get_provider(provider_name)
    quantity = quantity or int(profile.get("commercial", {}).get("ideal_order_quantity") or 1)

    offers: list[Offer] = []
    missing: list[str] = []
    for offer_id in offer_ids:
        try:
            offer = provider.get_offer(offer_id)
        except ProviderError as exc:
            missing.append(f"{offer_id}: {exc}")
            continue
        if offer:
            offers.append(offer)
        else:
            missing.append(f"{offer_id}: not found")

    if not offers:
        return {"status": "no_offers", "missing": missing, "comparison": []}

    # Hard filters run so their reasons are reported, but both groups are
    # returned: the buyer asked about these specific listings.
    passed, rejected = scoring.rank(offers, profile, quantity=quantity, enforce_garment_class=False)
    rows = []
    for scored in passed + rejected:
        scored.landed_cost = costing.landed_cost(scored.offer, profile, quantity=quantity)
        rows.append(scored.as_dict())

    return {
        "status": "ok",
        "quantity_assumed": quantity,
        "comparison": rows,
        "landed_cost_comparison": costing.compare_landed(offers, profile, quantity=quantity),
        "missing": missing,
        "note": "Hard filters are reported but not applied here - you asked about these specific listings.",
    }
