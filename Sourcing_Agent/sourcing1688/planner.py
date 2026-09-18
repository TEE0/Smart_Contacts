"""Turns an English brief plus the buyer's profile into concrete 1688 queries.

A single keyword search is not a sourcing strategy. 1688's keyword index is
literal and its supplier population is fragmented: 卫衣 and 帽衫 are the same
garment but surface largely different factories, and adding 定制 (custom) to a
query swaps a catalogue of stock resellers for one of OEM workshops.

So the planner fans out deliberately:

* **primary**   - the brief plus the profile's standing preferences
* **variant**   - synonym phrasings of the same garment
* **material**  - the brief pinned to each required fabric, because fabric is
                  usually the thing buyers compromise on by accident
* **commercial**- the brief plus the buying model (定制 / 一件代发 / 小批量)
* **fallback**  - a deliberately broad query, so a narrow brief never returns
                  an empty shortlist with no explanation

Each plan carries its rationale, so the connecting AI can tell the buyer *why*
it searched what it searched rather than presenting results from nowhere.
"""

from __future__ import annotations

from typing import Any

from . import lexicon
from .models import SearchPlan


def _profile_terms(profile: dict[str, Any]) -> dict[str, list[str]]:
    style = profile.get("style", {})
    materials = profile.get("materials", {})
    commercial = profile.get("commercial", {})
    fit = profile.get("fit", {})

    audience = [t for a in style.get("audience", []) for t in lexicon.lookup(a)[:1]]
    # Only the first preferred fit reaches the query: a profile that lists both
    # "oversized" and "regular fit" describes acceptable outcomes, not a garment
    # that is somehow both, and 宽松常规版型 matches nothing on 1688.
    fits = [t for f in fit.get("preferred_fits", [])[:1] for t in lexicon.lookup(f)[:1]]
    fabrics = [t for m in materials.get("required_any", []) + materials.get("required_all", []) for t in lexicon.lookup(m)[:1]]
    seasons = [t for s in style.get("seasons", []) if s != "all season" for t in lexicon.lookup(s)[:1]]

    commercial_terms: list[str] = []
    if commercial.get("needs_customization"):
        commercial_terms.extend(lexicon.lookup("custom logo")[:1])
    if commercial.get("accepts_dropshipping"):
        commercial_terms.extend(lexicon.lookup("dropshipping")[:1])
    if int(commercial.get("max_moq") or 0) and int(commercial["max_moq"]) <= 50:
        commercial_terms.extend(lexicon.lookup("small batch")[:1])

    return {
        "audience": audience,
        "fits": fits,
        "fabrics": fabrics,
        "seasons": seasons,
        "commercial": commercial_terms,
    }


def _filters(profile: dict[str, Any]) -> dict[str, Any]:
    commercial = profile.get("commercial", {})
    filters: dict[str, Any] = {}
    floor = float(commercial.get("min_unit_price_cny") or 0)
    ceiling = float(commercial.get("max_unit_price_cny") or 0)
    if floor > 0:
        filters["price_start"] = floor
    if ceiling > 0:
        # Widen the upstream price ceiling by 25%: 1688 filters on the headline
        # price, but the price that matters is the tier unlocked at the buyer's
        # order quantity, which is routinely 15-25% lower.
        filters["price_end"] = round(ceiling * 1.25, 2)
    return filters


def _compose(*groups: list[str] | str) -> str:
    parts: list[str] = []
    for group in groups:
        for term in [group] if isinstance(group, str) else group:
            if term and term not in parts:
                parts.append(term)
    return lexicon.join_terms(parts)


def plan_queries(brief: str, profile: dict[str, Any], max_queries: int = 8) -> list[SearchPlan]:
    """Build the query set for one sourcing run."""
    brief = (brief or "").strip()
    terms = _profile_terms(profile)
    filters = _filters(profile)
    plans: list[SearchPlan] = []
    seen: set[str] = set()

    def add(query_zh: str, query_en: str, rationale: str, intent: str) -> None:
        query_zh = (query_zh or "").strip()
        if not query_zh or query_zh in seen:
            return
        seen.add(query_zh)
        plans.append(SearchPlan(query_zh=query_zh, query_en=query_en, rationale=rationale, intent=intent, filters=dict(filters)))

    brief_zh = lexicon.translate_phrase(brief) if brief else ""
    brief_terms = lexicon.extract_terms(brief)
    brief_garments = [m for m in brief_terms if m[1] == "garment"]

    # Fall back to the profile's standing categories when the brief names no garment.
    if not brief_garments:
        category_terms = [t for c in profile.get("style", {}).get("categories", []) for t in lexicon.lookup(c)[:1]]
    else:
        category_terms = [brief_garments[0][2][0]]

    # 1. Primary - brief plus the standing profile context.
    add(
        _compose(terms["audience"], terms["seasons"], terms["fits"], brief_zh or category_terms),
        brief or "profile categories",
        "Brief combined with your standing audience, season and fit preferences.",
        "primary",
    )

    # 2. Variants - alternate Chinese names for the same garment.
    for alt in lexicon.alternates(brief) if brief else []:
        add(
            _compose(terms["audience"], alt),
            brief,
            "Synonym phrasing - 1688 keyword search is literal, and alternate garment names reach different factories.",
            "variant",
        )
    if not brief_garments:
        for category in profile.get("style", {}).get("categories", [])[1:3]:
            for alt in lexicon.lookup(category)[:2]:
                add(_compose(terms["audience"], alt), category, f"Standing category {category!r} from your profile.", "variant")

    # 3. Material-pinned queries.
    for fabric_zh in terms["fabrics"][:2]:
        add(
            _compose(terms["audience"], fabric_zh, category_terms),
            f"{brief} ({fabric_zh})",
            "Fabric pinned explicitly - composition is the requirement buyers most often lose by accident.",
            "material",
        )

    # 4. Commercial-model queries (OEM vs stock vs dropship populate different shops).
    for commercial_zh in terms["commercial"][:2]:
        add(
            _compose(category_terms, commercial_zh),
            f"{brief} ({commercial_zh})",
            "Buying model pinned - OEM workshops, stock wholesalers and dropship agents are near-disjoint seller populations.",
            "commercial",
        )

    # 5. Deliberate fallback so a narrow brief still yields a market view.
    add(
        _compose(category_terms),
        brief or "category only",
        "Deliberately broad - establishes the market price band the narrower queries are judged against.",
        "fallback",
    )

    return plans[:max_queries]


def explain_plan(brief: str, profile: dict[str, Any], plans: list[SearchPlan]) -> dict[str, Any]:
    """Diagnostics that let the buyer correct a bad translation before spending calls."""
    unknown = lexicon.unknown_terms(brief)
    return {
        "brief": brief,
        "recognized_terms": lexicon.describe_terms(brief),
        "unrecognized_terms": unknown,
        "translation_warning": (
            f"These words were not in the apparel lexicon and were dropped from the Chinese query: "
            f"{', '.join(unknown)}. If any of them matter, give me the Chinese term and I will use it verbatim."
            if unknown
            else ""
        ),
        "queries": [p.as_dict() for p in plans],
    }
