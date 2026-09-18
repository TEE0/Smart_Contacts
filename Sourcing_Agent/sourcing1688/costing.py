"""Landed cost per unit.

"Cheapest supplier" is the wrong question. A ¥19 tee from a 2-piece-MOQ reseller
and a ¥27 tee from a factory at 500 units are not comparable numbers: freight is
charged on weight, duty on declared value, and the agent's percentage applies to
goods value only. Two listings that look 40% apart routinely land within pennies
of each other, and the ranking flips once the buyer's real order quantity, mode
of shipping and destination duty rate are applied.

So the agent always ranks on landed cost, and always shows the arithmetic.

Every rate is an assumption, and every assumption is returned in the result. The
numbers are deliberately not presented as quotes - a freight forwarder's actual
rate card will differ, and the buyer should substitute theirs.
"""

from __future__ import annotations

from typing import Any

from .config import CostingConfig, get_settings
from .models import Offer

# Rough per-unit shipping weights (kg) by garment class, used when a listing
# does not publish one. Under-estimating freight flatters cheap heavy goods, so
# these lean slightly conservative.
DEFAULT_WEIGHTS_KG = {
    "t-shirt": 0.18,
    "polo shirt": 0.22,
    "shirt": 0.25,
    "hoodie": 0.55,
    "sweatshirt": 0.45,
    "sweater": 0.40,
    "cardigan": 0.38,
    "jacket": 0.70,
    "puffer jacket": 0.90,
    "coat": 1.10,
    "jeans": 0.60,
    "cargo pants": 0.55,
    "trousers": 0.45,
    "shorts": 0.25,
    "joggers": 0.40,
    "dress": 0.35,
    "skirt": 0.28,
    "leggings": 0.20,
    "yoga set": 0.30,
    "swimwear": 0.12,
    "socks": 0.05,
    "cap": 0.10,
    "bag": 0.20,
    "default": 0.35,
}

_KEYWORD_WEIGHTS = [
    ("羽绒", 0.90), ("棉服", 0.85), ("大衣", 1.10), ("风衣", 0.80), ("夹克", 0.70),
    ("牛仔裤", 0.60), ("工装裤", 0.55), ("卫衣", 0.55), ("毛衣", 0.40), ("开衫", 0.38),
    ("连衣裙", 0.35), ("半身裙", 0.28), ("衬衫", 0.25), ("短裤", 0.25), ("打底裤", 0.20),
    ("瑜伽", 0.30), ("t恤", 0.18), ("背心", 0.15), ("泳衣", 0.12), ("帽", 0.10), ("袜", 0.05),
]


def estimate_unit_weight(offer: Offer, category_hint: str = "") -> tuple[float, str]:
    """(kg, source) - published weight where available, otherwise inferred."""
    if offer.unit_weight_kg and offer.unit_weight_kg > 0:
        return offer.unit_weight_kg, "published by supplier"

    haystack = (offer.title + " " + " ".join(offer.category_path) + " " + category_hint).lower()
    for marker, weight in _KEYWORD_WEIGHTS:
        if marker in haystack:
            return weight, f"inferred from garment type ({marker})"

    hint = (category_hint or "").lower().strip()
    if hint in DEFAULT_WEIGHTS_KG:
        return DEFAULT_WEIGHTS_KG[hint], f"inferred from category hint ({hint})"
    return DEFAULT_WEIGHTS_KG["default"], "generic apparel default - supply a real weight for accuracy"


def landed_cost(
    offer: Offer,
    profile: dict[str, Any],
    *,
    quantity: int | None = None,
    shipping_mode: str | None = None,
    config: CostingConfig | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-unit landed cost with every component and assumption itemised."""
    settings = get_settings()
    cfg = config or settings.costing
    overrides = overrides or {}
    commercial = profile.get("commercial", {})
    logistics = profile.get("logistics", {})

    quantity = int(quantity or commercial.get("ideal_order_quantity") or 1)
    quantity = max(quantity, offer.moq or 1)
    mode = (shipping_mode or logistics.get("shipping_mode") or "air").lower()

    fx = float(overrides.get("fx_cny_per_unit") or cfg.fx_cny_per_unit)
    currency = overrides.get("currency") or profile.get("identity", {}).get("currency") or cfg.currency
    duty_pct = overrides.get("duty_pct")
    if duty_pct is None:
        duty_pct = logistics.get("duty_pct")
    if duty_pct is None:
        duty_pct = cfg.duty_pct
    tax_pct = overrides.get("import_tax_pct")
    if tax_pct is None:
        tax_pct = logistics.get("import_tax_pct")
    if tax_pct is None:
        tax_pct = cfg.import_tax_pct

    rates = {
        "express": float(overrides.get("express_rate_per_kg") or cfg.express_rate_per_kg),
        "air": float(overrides.get("air_rate_per_kg") or cfg.air_rate_per_kg),
        "sea": float(overrides.get("sea_rate_per_kg") or cfg.sea_rate_per_kg),
    }
    freight_rate = rates.get(mode, rates["air"])

    unit_price_cny = offer.price_at_quantity(quantity)
    goods_cny = unit_price_cny * quantity
    domestic_cny = float(overrides.get("domestic_freight_cny") or cfg.domestic_freight_cny)
    agent_pct = float(overrides.get("agent_fee_pct") or cfg.agent_fee_pct)
    agent_cny = goods_cny * agent_pct / 100.0

    weight_kg, weight_source = estimate_unit_weight(offer, str(overrides.get("category_hint") or ""))
    weight_kg = float(overrides.get("unit_weight_kg") or weight_kg)
    total_weight = weight_kg * quantity

    goods_value = goods_cny / fx
    freight = total_weight * freight_rate
    duty = goods_value * float(duty_pct) / 100.0
    # Import VAT/GST is levied on goods + freight + duty in most regimes.
    import_tax = (goods_value + freight + duty) * float(tax_pct) / 100.0
    qc_fee = float(overrides.get("qc_fee") or cfg.qc_fee_per_order)

    total = goods_value + (domestic_cny + agent_cny) / fx + freight + duty + import_tax + qc_fee
    per_unit = total / quantity if quantity else 0.0

    return {
        "quantity": quantity,
        "currency": currency,
        "shipping_mode": mode,
        "unit_price_cny": round(unit_price_cny, 2),
        "landed_unit_cost": round(per_unit, 2),
        "landed_total": round(total, 2),
        "uplift_vs_ex_works_pct": round((per_unit / (unit_price_cny / fx) - 1) * 100, 1) if unit_price_cny else 0.0,
        # Both scales are returned explicitly. A breakdown in order totals sitting
        # next to a per-unit headline is the kind of ambiguity that gets a buyer
        # to quote the wrong number to their own customer.
        "breakdown_order_total": {
            "goods": round(goods_value, 2),
            "domestic_freight_cn": round(domestic_cny / fx, 2),
            "agent_fee": round(agent_cny / fx, 2),
            "international_freight": round(freight, 2),
            "duty": round(duty, 2),
            "import_tax": round(import_tax, 2),
            "qc_inspection": round(qc_fee, 2),
        },
        "breakdown_per_unit": {
            "goods": round(goods_value / quantity, 3),
            "domestic_freight_cn": round(domestic_cny / fx / quantity, 3),
            "agent_fee": round(agent_cny / fx / quantity, 3),
            "international_freight": round(freight / quantity, 3),
            "duty": round(duty / quantity, 3),
            "import_tax": round(import_tax / quantity, 3),
            "qc_inspection": round(qc_fee / quantity, 3),
        },
        "assumptions": {
            "fx_cny_per_unit": fx,
            "unit_weight_kg": round(weight_kg, 3),
            "unit_weight_source": weight_source,
            "chargeable_weight_kg": round(total_weight, 2),
            "freight_rate_per_kg": freight_rate,
            "agent_fee_pct": agent_pct,
            "duty_pct": float(duty_pct),
            "import_tax_pct": float(tax_pct),
            "volumetric_divisor": cfg.volumetric_divisor,
            "note": (
                "Estimates, not quotes. Freight is charged on the greater of actual and volumetric weight - "
                "supply real carton dimensions and your forwarder's rate card before committing."
            ),
        },
    }


def compare_landed(offers: list[Offer], profile: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    """Landed cost for several offers, cheapest first.

    This is where headline-price ranking and real ranking usually diverge.
    """
    rows = []
    for offer in offers:
        cost = landed_cost(offer, profile, **kwargs)
        rows.append(
            {
                "offer_id": offer.offer_id,
                "title": offer.title,
                "supplier": offer.supplier.name,
                "unit_price_cny": cost["unit_price_cny"],
                "landed_unit_cost": cost["landed_unit_cost"],
                "currency": cost["currency"],
                "quantity": cost["quantity"],
                "uplift_pct": cost["uplift_vs_ex_works_pct"],
            }
        )
    return sorted(rows, key=lambda r: r["landed_unit_cost"])
