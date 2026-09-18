"""Supplier vetting: the part that stops a cheap listing becoming a bad order.

Everything here is a rule with a stated reason, not a black-box score. Sourcing
decisions get argued with a business partner, and "the model liked it" does not
survive that conversation.

The signals are the ones 1688 actually exposes and that experienced buyers
actually use:

* 诚信通 / TrustPass and 实力商家 years - cheap proxies for "has something to lose"
* 复购率 (repurchase rate) - the single most predictive field on the platform,
  because a wholesale buyer only reorders from a supplier that shipped correctly
* response rate and refund rate - operational reliability
* factory vs trading company - a trader adds margin and a layer of telephone
  between you and the machine that makes your garment; sometimes that is worth
  paying for, often it is not
* price relative to the market band - a listing far below its peers is not a
  bargain, it is a different (usually worse) product
"""

from __future__ import annotations

import statistics
from typing import Any

from .models import Offer

# Phrases that signal counterfeit or grey-market goods. Ordering a "replica"
# is a legal problem for the buyer, not a quality trade-off, so these are
# treated as disqualifying rather than as a score penalty.
COUNTERFEIT_MARKERS = ("a货", "高仿", "精仿", "replica", "1:1", "原单尾货", "复刻", "莆田")
TRADER_MARKERS = ("商行", "贸易", "商贸", "供应链", "工作室", "个体")
FACTORY_MARKERS = ("制衣", "服饰有限公司", "针织", "制帽", "厂", "织造", "实业")


def infer_business_type(offer: Offer) -> str:
    """Factory or trader, inferred from the company name when unstated.

    1688 does not reliably flag this, but Chinese company naming conventions do:
    a 制衣厂 makes garments, a 商行 resells them.
    """
    declared = (offer.supplier.business_type or "").strip().lower()
    if declared in {"factory", "工厂", "生产厂家"}:
        return "factory"
    if declared in {"trader", "trading", "贸易商", "经销批发"}:
        return "trader"
    name = offer.supplier.name or ""
    if any(marker in name for marker in TRADER_MARKERS):
        return "trader"
    if any(marker in name for marker in FACTORY_MARKERS):
        return "factory"
    return "unknown"


def market_price_band(offers: list[Offer], quantity: int) -> tuple[float, float]:
    """Median and interquartile-ish spread of the candidate set.

    Used to judge an individual price as an outlier. Computed from the search
    results themselves rather than from a static table, because the right price
    for a 280gsm tee has nothing to do with the right price for a chiffon dress.
    """
    prices = [o.price_at_quantity(quantity) for o in offers if o.price_at_quantity(quantity) > 0]
    if not prices:
        return 0.0, 0.0
    median = statistics.median(prices)
    spread = statistics.pstdev(prices) if len(prices) > 1 else 0.0
    return median, spread


def vet(offer: Offer, profile: dict[str, Any], *, market_median: float = 0.0, quantity: int | None = None) -> tuple[list[str], list[str]]:
    """Return (risk_flags, strengths) for one offer.

    Risk flags are advisory - they explain a low score. Disqualification is a
    separate step (:func:`sourcing1688.scoring.hard_filter`), because a buyer
    should be able to see a risky-but-interesting supplier if they ask.
    """
    sup = offer.supplier
    rules = profile.get("supplier", {})
    commercial = profile.get("commercial", {})
    quantity = quantity or int(commercial.get("ideal_order_quantity") or 1)

    risks: list[str] = []
    strengths: list[str] = []

    text = f"{offer.title} {offer.title_en}".lower()
    for marker in COUNTERFEIT_MARKERS:
        if marker in text:
            risks.append(f"Listing language suggests counterfeit or grey-market stock ({marker!r}) - legal exposure on import.")
            break

    years = sup.years_on_platform
    if years and years < float(rules.get("min_years_on_platform") or 0):
        risks.append(f"Only {years:g} year(s) on 1688 (your floor is {rules.get('min_years_on_platform')}).")
    elif years >= 5:
        strengths.append(f"{years:g} years trading on 1688.")

    if not sup.is_trustpass:
        risks.append("No 诚信通 (TrustPass) membership - no platform deposit backing disputes.")
    elif sup.is_verified_factory:
        strengths.append("TrustPass plus 实力商家 / verified-factory status.")

    if sup.repurchase_rate:
        floor = float(rules.get("min_repurchase_rate") or 0)
        if sup.repurchase_rate < floor:
            risks.append(f"Repurchase rate {sup.repurchase_rate:.0%} is below your {floor:.0%} floor - buyers are not coming back.")
        elif sup.repurchase_rate >= 0.35:
            strengths.append(f"Repurchase rate {sup.repurchase_rate:.0%} - wholesale buyers reorder here.")
    else:
        risks.append("No repurchase-rate data published - the most useful reliability signal is missing.")

    if sup.response_rate and sup.response_rate < float(rules.get("min_response_rate") or 0):
        risks.append(f"Response rate {sup.response_rate:.0%} - expect slow replies during production.")

    if sup.refund_rate is not None and sup.refund_rate > float(rules.get("max_refund_rate") or 1):
        risks.append(f"Refund rate {sup.refund_rate:.0%} exceeds your ceiling.")

    if sup.composite_rating and sup.composite_rating < float(rules.get("min_composite_rating") or 0):
        risks.append(f"Composite rating {sup.composite_rating:.1f} below your {rules.get('min_composite_rating')} floor.")

    business_type = infer_business_type(offer)
    preferred = (rules.get("prefer_business_type") or "any").lower()
    if preferred != "any" and business_type != "unknown" and business_type != preferred:
        risks.append(f"Looks like a {business_type}, and you prefer a {preferred} (extra margin and an extra hop to the production line).")
    elif business_type == "factory" and preferred == "factory":
        strengths.append("Company name and profile read as a manufacturer, not a reseller.")

    if commercial.get("needs_customization") and not sup.supports_customization:
        risks.append("No customisation/OEM support flagged - private-label work may not be possible here.")
    elif sup.supports_customization:
        strengths.append("Supports OEM / custom logo work.")

    if commercial.get("needs_sample_first") and not sup.supports_sample:
        risks.append("No sample service flagged - you would be ordering production quantity unseen.")

    price = offer.price_at_quantity(quantity)
    floor_price = float(commercial.get("min_unit_price_cny") or 0)
    if floor_price and price and price < floor_price:
        risks.append(f"¥{price:.2f} is below your ¥{floor_price:.2f} sanity floor - usually thinner fabric, not a better deal.")
    if market_median and price and price < market_median * 0.5:
        risks.append(f"¥{price:.2f} is less than half the ¥{market_median:.2f} median for this search - verify fabric weight before sampling.")
    if market_median and price and price > market_median * 2:
        risks.append(f"¥{price:.2f} is more than double the ¥{market_median:.2f} median - confirm what justifies the premium.")

    if offer.moq > int(commercial.get("max_moq") or 10**9):
        risks.append(f"MOQ {offer.moq} exceeds your ceiling of {commercial.get('max_moq')}.")
    elif offer.moq <= 30:
        strengths.append(f"Low MOQ ({offer.moq}) - cheap to trial.")

    certs = {c.lower() for c in offer.certifications}
    cert_text = " ".join(offer.attributes.values()).lower()
    for cert in rules.get("preferred_certifications", []) or []:
        if cert.lower() in certs or cert.lower().replace("-", "") in cert_text.replace("-", ""):
            strengths.append(f"Carries {cert.upper()} certification.")

    if offer.sold_30d >= 1000:
        strengths.append(f"{offer.sold_30d} units moved in 30 days - the line is in active production.")
    elif offer.sold_30d and offer.sold_30d < 50:
        risks.append(f"Only {offer.sold_30d} units sold in 30 days - low-volume line, lead times may be unpredictable.")

    return risks, strengths


def trust_score(offer: Offer, profile: dict[str, Any]) -> tuple[float, str]:
    """0-1 supplier reliability score with a one-line justification."""
    sup = offer.supplier
    components: list[tuple[float, float]] = []  # (weight, value)

    components.append((0.30, min(sup.repurchase_rate / 0.45, 1.0) if sup.repurchase_rate else 0.25))
    components.append((0.20, min(sup.years_on_platform / 8.0, 1.0) if sup.years_on_platform else 0.15))
    components.append((0.15, min(sup.composite_rating / 5.0, 1.0) if sup.composite_rating else 0.5))
    components.append((0.15, sup.response_rate if sup.response_rate else 0.5))
    components.append((0.10, 1.0 if sup.is_trustpass else 0.0))
    components.append((0.10, 1.0 if sup.is_verified_factory else 0.35))

    total = sum(weight * value for weight, value in components)
    if sup.refund_rate is not None:
        total *= max(0.6, 1.0 - sup.refund_rate)

    preferred = (profile.get("supplier", {}).get("prefer_business_type") or "any").lower()
    if preferred != "any" and infer_business_type(offer) == preferred:
        total = min(1.0, total * 1.08)

    detail = (
        f"repurchase {sup.repurchase_rate:.0%}, {sup.years_on_platform:g}y on platform, "
        f"rating {sup.composite_rating:.1f}, {'TrustPass' if sup.is_trustpass else 'no TrustPass'}"
    )
    return max(0.0, min(1.0, total)), detail
