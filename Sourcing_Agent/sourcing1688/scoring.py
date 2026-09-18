"""Hard filters and explainable multi-criteria scoring.

The two stages answer different questions:

``hard_filter`` answers "may I buy this at all?" - MOQ ceiling, banned fabric,
blocked seller, missing certification, counterfeit language. A failure here is
binary and is reported with its reason, so a buyer who sees an empty shortlist
can immediately tell whether their constraints or the market are at fault.

``score`` answers "which of the survivors is best *for me*?" - a weighted sum of
normalised factors, where the weights come from the buyer's profile. Every
factor keeps its raw value, its weight and a sentence of justification, so the
ranking can always be explained line by line. No hidden tiebreakers.
"""

from __future__ import annotations

import re
from typing import Any

from . import lexicon
from .models import Offer, ScoredOffer, ScoreFactor
from .supplier import COUNTERFEIT_MARKERS, market_price_band, trust_score, vet

_PCT = re.compile(r"(\d{1,3})\s*%")
_GSM = re.compile(r"(\d{2,4})\s*(?:g|gsm|克)")

# Fibres treated as "natural" for the min_natural_fiber_pct constraint.
# Bare 绒 is deliberately absent: 摇粒绒 (fleece) and 加绒 (fleece-lined) are
# polyester finishes, so treating it as a natural fibre marker reports a
# poly-blend hoodie as 100% natural.
NATURAL_FIBERS = ("棉", "麻", "丝", "毛", "羊绒", "羽绒", "竹纤维", "莫代尔", "天丝",
                  "cotton", "linen", "silk", "wool", "cashmere")
SYNTHETIC_MARKERS = ("涤纶", "聚酯纤维", "腈纶", "尼龙", "锦纶", "氨纶", "polyester", "nylon", "acrylic", "spandex")


def natural_fiber_pct(offer: Offer) -> float | None:
    """Estimate natural-fibre content from the composition string.

    1688 composition text is free-form ("95%棉 5%氨纶", "全棉", "100%聚酯纤维"),
    so this parses percentages where present and falls back to qualitative
    markers. Returns None when nothing can be determined - an unknown value must
    not be silently scored as zero, or every under-documented listing gets
    eliminated for the wrong reason.
    """
    text = f"{offer.material_text} {' '.join(offer.attributes.values())}".strip()
    if not text:
        return None

    # Each percentage owns the text up to the next percentage, so the fibre name
    # in "95%棉 5%氨纶" is attributed to 棉 alone. A fixed-width lookahead double
    # counts the neighbour and quietly reports a pure-cotton tee as ~49% natural.
    matches = list(_PCT.finditer(text))
    pairs: list[tuple[float, str]] = []
    for index, match in enumerate(matches):
        pct = float(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pairs.append((pct, text[match.end() : end].lower()))

    if pairs:
        # Synthetic names win a tie: 涤纶/氨纶 are unambiguous fibre names, while
        # 棉 and 毛 appear inside compound words for finishes and wadding.
        natural = sum(pct for pct, seg in pairs
                      if any(f in seg for f in NATURAL_FIBERS) and not any(f in seg for f in SYNTHETIC_MARKERS))
        synthetic = sum(pct for pct, seg in pairs if any(f in seg for f in SYNTHETIC_MARKERS))
        total = natural + synthetic
        if total:
            return round(natural / total * 100, 1)

    low = text.lower()
    if "全棉" in low or "纯棉" in low or "100%cotton" in low.replace(" ", ""):
        return 100.0
    if any(marker in low for marker in SYNTHETIC_MARKERS) and not any(f in low for f in NATURAL_FIBERS):
        return 0.0
    if any(f in low for f in NATURAL_FIBERS):
        return None  # mentions a natural fibre but gives no ratio
    return None


def gsm(offer: Offer) -> int | None:
    text = f"{offer.title} {' '.join(offer.attributes.values())}"
    matches = [int(m.group(1)) for m in _GSM.finditer(text)]
    plausible = [m for m in matches if 80 <= m <= 900]
    return max(plausible) if plausible else None


def brief_garment_terms(brief: str, profile: dict[str, Any]) -> list[str]:
    """Chinese terms for the garment class the buyer actually asked for.

    Falls back to the profile's standing categories when the brief names none.
    """
    garments = [m for m in lexicon.extract_terms(brief or "") if m[1] == "garment"]
    if garments:
        return [zh for _p, _g, terms in garments for zh in terms]
    return [zh for c in profile.get("style", {}).get("categories", []) or [] for zh in lexicon.lookup(c)]


def off_brief_reason(offer: Offer, garment_terms: list[str]) -> str | None:
    """Reject listings that are not the garment class in question.

    Keyword fan-out reliably drags in adjacent products - a search for an
    embroidered cotton tee returns embroidered cotton *caps*, which score well
    on price, fabric and supplier trust while being the wrong product entirely.
    Relevance has to be a gate, not a weighted factor.
    """
    if not garment_terms:
        return None
    haystack = offer.searchable_text()
    if any(term.lower() in haystack for term in garment_terms if term):
        return None
    return f"Not the garment you asked for - the listing matches none of: {', '.join(dict.fromkeys(garment_terms))}."


def hard_filter(
    offer: Offer,
    profile: dict[str, Any],
    quantity: int | None = None,
    garment_terms: list[str] | None = None,
) -> list[str]:
    """Reasons this offer is disqualified outright. Empty list means it passed."""
    reasons: list[str] = []
    commercial = profile.get("commercial", {})
    materials = profile.get("materials", {})
    style = profile.get("style", {})
    supplier_rules = profile.get("supplier", {})
    quantity = quantity or int(commercial.get("ideal_order_quantity") or 1)

    haystack = offer.searchable_text()

    off_brief = off_brief_reason(offer, garment_terms or [])
    if off_brief:
        reasons.append(off_brief)

    for marker in COUNTERFEIT_MARKERS:
        if marker in haystack:
            reasons.append(f"Counterfeit/grey-market language in the listing ({marker!r}).")
            break

    for banned in style.get("banned_keywords", []) or []:
        if banned and banned.lower() in haystack:
            reasons.append(f"Contains your banned keyword {banned!r}.")

    for required in style.get("must_have_keywords", []) or []:
        zh = lexicon.lookup(required)
        variants = [required.lower(), *[z.lower() for z in zh]]
        if not any(v in haystack for v in variants if v):
            reasons.append(f"Missing your required keyword {required!r}.")

    if offer.supplier.seller_id and offer.supplier.seller_id in (supplier_rules.get("blocked_sellers") or []):
        reasons.append(f"Seller {offer.supplier.seller_id} is on your block list.")

    max_moq = int(commercial.get("max_moq") or 0)
    if max_moq and offer.moq > max_moq:
        reasons.append(f"MOQ {offer.moq} exceeds your ceiling of {max_moq}.")

    price = offer.price_at_quantity(quantity)
    ceiling = float(commercial.get("max_unit_price_cny") or 0)
    if ceiling and price > ceiling:
        reasons.append(f"¥{price:.2f}/unit at {quantity} pieces exceeds your ¥{ceiling:.2f} ceiling.")

    budget = float(commercial.get("budget_total_cny") or 0)
    if budget and price and price * max(offer.moq, quantity) > budget:
        reasons.append(
            f"Minimum viable order (¥{price:.2f} x {max(offer.moq, quantity)}) exceeds your ¥{budget:.0f} budget."
        )

    for banned in materials.get("banned", []) or []:
        variants = [banned.lower(), *[z.lower() for z in lexicon.lookup(banned)]]
        if any(v in haystack for v in variants if v):
            reasons.append(f"Contains banned material {banned!r}.")
            break

    for required in materials.get("required_all", []) or []:
        variants = [required.lower(), *[z.lower() for z in lexicon.lookup(required)]]
        if not any(v in haystack for v in variants if v):
            reasons.append(f"Does not state required material {required!r}.")

    required_any = materials.get("required_any", []) or []
    if required_any:
        matched = False
        for required in required_any:
            variants = [required.lower(), *[z.lower() for z in lexicon.lookup(required)]]
            if any(v in haystack for v in variants if v):
                matched = True
                break
        if not matched:
            reasons.append(f"States none of your acceptable materials ({', '.join(required_any)}).")

    min_natural = int(materials.get("min_natural_fiber_pct") or 0)
    if min_natural:
        measured = natural_fiber_pct(offer)
        if measured is not None and measured < min_natural:
            reasons.append(f"Natural fibre content ~{measured:.0f}% is below your {min_natural}% floor.")

    min_gsm, max_gsm = int(materials.get("min_gsm") or 0), int(materials.get("max_gsm") or 0)
    measured_gsm = gsm(offer)
    if min_gsm and measured_gsm is not None and measured_gsm < min_gsm:
        reasons.append(f"{measured_gsm}gsm is lighter than your {min_gsm}gsm floor.")
    if max_gsm and measured_gsm is not None and measured_gsm > max_gsm:
        reasons.append(f"{measured_gsm}gsm is heavier than your {max_gsm}gsm ceiling.")

    for cert in supplier_rules.get("required_certifications", []) or []:
        cert_text = " ".join(offer.attributes.values()).lower() + " " + " ".join(offer.certifications).lower()
        if cert.lower().replace("-", "") not in cert_text.replace("-", ""):
            reasons.append(f"Does not evidence required certification {cert.upper()}.")

    return reasons


# ------------------------------------------------------------------- factors


def _price_factor(offer: Offer, profile: dict[str, Any], quantity: int) -> ScoreFactor:
    commercial = profile["commercial"]
    target = float(commercial.get("target_unit_price_cny") or 0)
    ceiling = float(commercial.get("max_unit_price_cny") or 0)
    floor = float(commercial.get("min_unit_price_cny") or 0)
    price = offer.price_at_quantity(quantity)

    if not price:
        return ScoreFactor("price", 0, 0.3, 0, "No usable price published.")
    if not target:
        return ScoreFactor("price", 0, 0.5, 0, f"¥{price:.2f}/unit, no target set.")

    if price <= target:
        # Below target is good, but only down to the sanity floor - cheaper than
        # the floor is a different product, not a better deal.
        if floor and price < floor:
            raw = 0.45
            detail = f"¥{price:.2f} is under your ¥{floor:.2f} sanity floor - treated as a quality risk, not a win."
        else:
            headroom = max(target - (floor or 0), 1e-6)
            raw = 0.85 + 0.15 * min((target - price) / headroom, 1.0)
            detail = f"¥{price:.2f}/unit at {quantity} pcs, at or under your ¥{target:.2f} target."
    else:
        span = max((ceiling or target * 1.5) - target, 1e-6)
        raw = max(0.0, 1.0 - (price - target) / span) * 0.8
        detail = f"¥{price:.2f}/unit at {quantity} pcs, ¥{price - target:.2f} over your ¥{target:.2f} target."

    return ScoreFactor("price", 0, round(raw, 4), 0, detail)


def _material_factor(offer: Offer, profile: dict[str, Any]) -> ScoreFactor:
    materials = profile["materials"]
    haystack = offer.searchable_text()
    wanted = (materials.get("required_any") or []) + (materials.get("required_all") or [])

    hits: list[str] = []
    for material in wanted:
        variants = [material.lower(), *[z.lower() for z in lexicon.lookup(material)]]
        if any(v in haystack for v in variants if v):
            hits.append(material)

    natural = natural_fiber_pct(offer)
    min_natural = int(materials.get("min_natural_fiber_pct") or 0)

    if wanted:
        raw = len(hits) / len(wanted)
        detail = f"Matches {len(hits)}/{len(wanted)} of your materials ({', '.join(hits) or 'none'})."
    elif natural is not None:
        raw = min(natural / 100.0, 1.0)
        detail = f"~{natural:.0f}% natural fibre."
    else:
        raw = 0.45
        detail = "Composition not stated - ask the supplier before sampling."

    if min_natural and natural is not None:
        raw = raw * (1.0 if natural >= min_natural else 0.5)
        detail += f" Natural fibre ~{natural:.0f}% vs your {min_natural}% floor."
    elif min_natural and natural is None:
        raw *= 0.8
        detail += " Natural-fibre share unverifiable from the listing."

    measured_gsm = gsm(offer)
    if measured_gsm:
        detail += f" Listed at {measured_gsm}gsm."

    return ScoreFactor("material_match", 0, round(min(raw, 1.0), 4), 0, detail)


def _style_factor(offer: Offer, profile: dict[str, Any], brief: str = "") -> ScoreFactor:
    style = profile["style"]
    fit = profile["fit"]
    haystack = offer.searchable_text()

    wanted: list[str] = []
    wanted.extend(style.get("nice_to_have_keywords") or [])
    wanted.extend(fit.get("preferred_fits") or [])
    wanted.extend(style.get("preferred_colors") or [])
    if brief:
        wanted.extend(p for p, group, _zh in lexicon.extract_terms(brief) if group in {"detail", "fit", "garment"})

    wanted = list(dict.fromkeys(w for w in wanted if w))
    if not wanted:
        return ScoreFactor("style_match", 0, 0.5, 0, "No style preferences recorded.")

    hits = []
    for term in wanted:
        variants = [term.lower(), *[z.lower() for z in lexicon.lookup(term)]]
        if any(v in haystack for v in variants if v):
            hits.append(term)

    penalty = 0.0
    avoided = []
    for term in (fit.get("avoid_fits") or []) + (style.get("avoid_colors") or []):
        variants = [term.lower(), *[z.lower() for z in lexicon.lookup(term)]]
        if any(v in haystack for v in variants if v):
            avoided.append(term)
            penalty += 0.15

    raw = max(0.0, min(1.0, len(hits) / max(len(wanted), 1) * 1.4 - penalty))
    detail = f"Matches {len(hits)}/{len(wanted)} style cues ({', '.join(hits[:4]) or 'none'})."
    if avoided:
        detail += f" Mentions what you avoid: {', '.join(avoided)}."
    return ScoreFactor("style_match", 0, round(raw, 4), 0, detail)


def _moq_factor(offer: Offer, profile: dict[str, Any]) -> ScoreFactor:
    commercial = profile["commercial"]
    ceiling = int(commercial.get("max_moq") or 0)
    planned = int(commercial.get("ideal_order_quantity") or 1)

    if offer.moq <= 1:
        return ScoreFactor("moq_fit", 0, 1.0, 0, "No practical minimum - single units available.")
    if ceiling and offer.moq > ceiling:
        return ScoreFactor("moq_fit", 0, 0.0, 0, f"MOQ {offer.moq} above your {ceiling} ceiling.")

    raw = max(0.0, 1.0 - offer.moq / max(ceiling or planned, 1))
    # A MOQ at or under the planned order is effectively free of friction.
    if offer.moq <= planned:
        raw = max(raw, 0.7)
    return ScoreFactor("moq_fit", 0, round(min(raw, 1.0), 4), 0, f"MOQ {offer.moq} against your {planned}-unit plan.")


def _traction_factor(offer: Offer) -> ScoreFactor:
    sold = offer.sold_30d or offer.sold_total
    reviews = offer.review_count
    if not sold and not reviews:
        return ScoreFactor("traction", 0, 0.3, 0, "No sales or review history published.")
    sales_score = min((sold or 0) / 2000.0, 1.0)
    review_score = min((reviews or 0) / 500.0, 1.0)
    raw = 0.6 * sales_score + 0.4 * review_score
    return ScoreFactor("traction", 0, round(raw, 4), 0, f"{sold} sold recently across {reviews} reviews.")


def _customization_factor(offer: Offer, profile: dict[str, Any]) -> ScoreFactor:
    commercial = profile["commercial"]
    needed = bool(commercial.get("needs_customization"))
    supports = offer.supplier.supports_customization or any(
        marker in offer.searchable_text() for marker in ("定制", "来图", "oem", "贴牌", "打样")
    )
    if not needed:
        return ScoreFactor("customization", 0, 0.5, 0, "Customisation not required by your profile.")
    raw = 1.0 if supports else 0.1
    return ScoreFactor(
        "customization", 0, raw, 0,
        "Listing/seller advertises custom or OEM work." if supports else "No sign this seller does private-label work.",
    )


def score_offer(
    offer: Offer,
    profile: dict[str, Any],
    *,
    brief: str = "",
    quantity: int | None = None,
    market_median: float = 0.0,
    apply_hard_filter: bool = True,
    garment_terms: list[str] | None = None,
) -> ScoredOffer:
    commercial = profile.get("commercial", {})
    quantity = quantity or int(commercial.get("ideal_order_quantity") or 1)
    weights = profile.get("weights", {})

    trust_raw, trust_detail = trust_score(offer, profile)
    factors = [
        _price_factor(offer, profile, quantity),
        _material_factor(offer, profile),
        ScoreFactor("supplier_trust", 0, round(trust_raw, 4), 0, trust_detail),
        _style_factor(offer, profile, brief),
        _moq_factor(offer, profile),
        _traction_factor(offer),
        _customization_factor(offer, profile),
    ]

    total = 0.0
    for factor in factors:
        factor.weight = float(weights.get(factor.name, 0.0))
        factor.contribution = round(factor.weight * factor.raw_score, 6)
        total += factor.contribution

    risks, strengths = vet(offer, profile, market_median=market_median, quantity=quantity)
    rejected = hard_filter(offer, profile, quantity, garment_terms) if apply_hard_filter else []

    return ScoredOffer(
        offer=offer,
        score=round(total, 4),
        factors=factors,
        risk_flags=risks,
        strengths=strengths,
        rejected_reasons=rejected,
    )


def rank(
    offers: list[Offer],
    profile: dict[str, Any],
    *,
    brief: str = "",
    quantity: int | None = None,
    apply_hard_filter: bool = True,
    enforce_garment_class: bool = True,
) -> tuple[list[ScoredOffer], list[ScoredOffer]]:
    """Score every offer and split into (passed, rejected), both ranked."""
    quantity = quantity or int(profile.get("commercial", {}).get("ideal_order_quantity") or 1)
    median, _spread = market_price_band(offers, quantity)
    garment_terms = brief_garment_terms(brief, profile) if enforce_garment_class else []

    scored = [
        score_offer(
            o, profile, brief=brief, quantity=quantity, market_median=median,
            apply_hard_filter=apply_hard_filter, garment_terms=garment_terms,
        )
        for o in offers
    ]
    passed = sorted([s for s in scored if s.passed], key=lambda s: s.score, reverse=True)
    rejected = sorted([s for s in scored if not s.passed], key=lambda s: s.score, reverse=True)
    return passed, rejected


# ------------------------------------------------------- rejection diagnosis

# Maps a rejection sentence back to the profile field that caused it, so an
# empty shortlist can name the setting to change instead of leaving the buyer
# to guess which of a dozen constraints bit.
_REASON_TAGS: tuple[tuple[str, str], ...] = (
    ("not the garment", "off_brief"),
    ("budget", "commercial.budget_total_cny"),
    ("moq", "commercial.max_moq"),
    ("/unit at", "commercial.max_unit_price_cny"),
    ("natural fibre", "materials.min_natural_fiber_pct"),
    ("gsm", "materials.min_gsm / materials.max_gsm"),
    ("banned material", "materials.banned"),
    ("required material", "materials.required_all"),
    ("acceptable materials", "materials.required_any"),
    ("banned keyword", "style.banned_keywords"),
    ("required keyword", "style.must_have_keywords"),
    ("certification", "supplier.required_certifications"),
    ("block list", "supplier.blocked_sellers"),
    ("counterfeit", "counterfeit_guard"),
)


def classify_reason(reason: str) -> str:
    low = reason.lower()
    for needle, tag in _REASON_TAGS:
        if needle in low:
            return tag
    return "other"


def diagnose_rejections(rejected: list[ScoredOffer], profile: dict[str, Any]) -> dict[str, Any]:
    """Explain what is actually blocking, and what relaxing it would take.

    Distinguishes "your search found the wrong products" from "your search found
    the right products and your constraints excluded them" - the remedies are
    opposite, and conflating them is how buyers end up loosening the wrong dial.
    """
    on_brief = [s for s in rejected if not any(classify_reason(r) == "off_brief" for r in s.rejected_reasons)]
    off_brief_count = len(rejected) - len(on_brief)

    counts: dict[str, int] = {}
    for scored in on_brief:
        for tag in {classify_reason(r) for r in scored.rejected_reasons}:
            counts[tag] = counts.get(tag, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    suggestions: list[str] = []
    commercial = profile.get("commercial", {})
    quantity = int(commercial.get("ideal_order_quantity") or 1)

    def blocked_by(tag: str) -> list[ScoredOffer]:
        """Only the listings this constraint actually eliminated.

        Scoping matters: averaging over every rejected listing produces advice
        like "raise your budget to 7350" when the budget is already 8000.
        """
        return [s for s in on_brief if any(classify_reason(r) == tag for r in s.rejected_reasons)]

    for tag, count in ranked[:3]:
        group = blocked_by(tag)
        if tag == "commercial.budget_total_cny" and group:
            needed = min(s.offer.price_at_quantity(quantity) * max(s.offer.moq, quantity) for s in group)
            budget = float(commercial.get("budget_total_cny") or 0)
            affordable_qty = int(budget / max(min(s.offer.price_at_quantity(quantity) for s in group), 0.01))
            suggestions.append(
                f"{count} on-brief listing(s) blocked by your ¥{budget:.0f} budget. The cheapest of them needs "
                f"¥{needed:.0f} at {quantity} units - raise the budget to about ¥{needed * 1.05:.0f}, "
                f"or drop the planned quantity to roughly {affordable_qty} units."
            )
        elif tag == "commercial.max_unit_price_cny" and group:
            cheapest = min(s.offer.price_at_quantity(quantity) for s in group)
            ceiling = float(commercial.get("max_unit_price_cny") or 0)
            suggestions.append(
                f"{count} on-brief listing(s) blocked by your ¥{ceiling:.2f} unit-price ceiling. "
                f"The cheapest of them is ¥{cheapest:.2f} at {quantity} units - a ¥{cheapest - ceiling:.2f} gap."
            )
        elif tag == "commercial.max_moq" and group:
            lowest = min(s.offer.moq for s in group)
            suggestions.append(
                f"{count} on-brief listing(s) blocked by your MOQ ceiling of {commercial.get('max_moq')}. "
                f"The lowest MOQ among them is {lowest}."
            )
        elif tag == "materials.min_natural_fiber_pct":
            floor = profile.get("materials", {}).get("min_natural_fiber_pct")
            best = max((natural_fiber_pct(s.offer) or 0) for s in group) if group else 0
            suggestions.append(
                f"{count} on-brief listing(s) fall below your {floor}% natural-fibre floor "
                f"(best of them is ~{best:.0f}%) - this category is largely synthetic, so the floor is doing real work."
            )
        else:
            suggestions.append(f"{count} on-brief listing(s) blocked by {tag}.")

    return {
        # Shape is shared with the no-results diagnosis so a caller never has to
        # branch on which kind of empty answer it received.
        "cause": (ranked[0][0] if ranked else ("off_brief" if off_brief_count else "none")),
        "detail": suggestions[0] if suggestions else "No hard constraint eliminated an on-brief listing.",
        "off_brief_count": off_brief_count,
        "on_brief_blocked_count": len(on_brief),
        "blocking_constraints": [{"setting": tag, "listings_blocked": count} for tag, count in ranked],
        "suggestions": suggestions,
        "interpretation": (
            "Your constraints are binding - the market has matching products you have ruled out."
            if on_brief
            else "The searches did not surface the right garment class at all - refine the brief rather than the constraints."
        ),
    }
