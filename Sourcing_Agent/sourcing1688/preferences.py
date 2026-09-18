"""The buyer's preference profile - the "personalisation" half of the agent.

A profile is a plain nested dict so it round-trips to JSON, survives partial
updates from an LLM, and can be hand-edited. Everything the planner, the hard
filters, the scorer and the cost model need comes from here.

Two kinds of preference are deliberately separated:

* ``hard`` constraints eliminate an offer outright (MOQ above ceiling, banned
  fabric, blocked seller). They are cheap, auditable, and never traded off.
* ``weights`` express what "better" means when several offers survive. They
  drive the score, so tuning them changes ranking without hiding anything.

Mixing the two is the classic sourcing-tool mistake: a shopper who says "never
above 500 MOQ" does not mean "prefer lower MOQ".
"""

from __future__ import annotations

import copy
from typing import Any

from .store import read_json, write_json

PROFILE_FILE = "profile.json"

DEFAULT_PROFILE: dict[str, Any] = {
    "version": 1,
    "identity": {
        "buyer_type": "small_brand",          # reseller | small_brand | boutique | dropshipper | wholesaler
        "destination_country": "GB",
        "destination_city": "",
        "currency": "USD",
        "language": "en",
    },
    "style": {
        "categories": ["t-shirt", "hoodie"],  # lexicon garment keys
        "aesthetic": ["streetwear", "minimal"],
        "must_have_keywords": [],
        "nice_to_have_keywords": ["heavyweight", "combed cotton"],
        "banned_keywords": ["fast fashion", "counterfeit", "replica", "A货", "高仿"],
        "preferred_colors": ["black", "white", "navy"],
        "avoid_colors": [],
        "seasons": ["all season"],
        "audience": ["unisex"],
    },
    "fit": {
        "size_region": "UK",                  # UK | EU | US | ASIA
        "size_range": ["S", "M", "L", "XL"],
        "preferred_fits": ["oversized", "regular fit"],
        "avoid_fits": ["slim fit"],
        "asian_sizing_tolerance": "size_up",  # size_up | strict | any
    },
    "materials": {
        "required_any": [],                   # offer must mention at least one
        "required_all": [],                   # offer must mention every one
        "banned": ["pu leather"],
        "min_natural_fiber_pct": 60,          # 0 disables the check
        "min_gsm": 0,                         # 0 disables; heavyweight tees are 220-280
        "max_gsm": 0,
    },
    "commercial": {
        "target_unit_price_cny": 35.0,
        "max_unit_price_cny": 60.0,
        "min_unit_price_cny": 8.0,            # absurdly cheap = quality/counterfeit risk
        "max_moq": 100,
        "ideal_order_quantity": 200,
        "budget_total_cny": 8000.0,
        "needs_sample_first": True,
        "needs_customization": True,          # logo / private label
        "accepts_dropshipping": False,
    },
    "supplier": {
        "min_years_on_platform": 2.0,
        "min_repurchase_rate": 0.25,
        "min_response_rate": 0.80,
        "min_composite_rating": 4.0,
        "max_refund_rate": 0.08,
        "require_trustpass": True,            # 诚信通
        "require_verified_factory": False,    # 实力商家 / 验厂
        "prefer_business_type": "factory",    # factory | trader | any
        "preferred_provinces": ["广东", "浙江", "福建"],
        "blocked_sellers": [],
        "required_certifications": [],
        "preferred_certifications": ["oeko-tex", "bsci"],
    },
    "logistics": {
        "shipping_mode": "air",               # express | air | sea
        "consolidate_with_agent": True,
        "lead_time_days_max": 35,
        "duty_pct": None,                     # None -> fall back to server config
        "import_tax_pct": None,
    },
    "weights": {
        "price": 0.24,
        "material_match": 0.18,
        "supplier_trust": 0.20,
        "style_match": 0.14,
        "moq_fit": 0.10,
        "traction": 0.08,
        "customization": 0.06,
    },
    "notes": "",
}

# Only these top-level sections may be written to, so a malformed patch from a
# model cannot invent a section the rest of the code never reads.
_SECTIONS = set(DEFAULT_PROFILE.keys())


def default_profile() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_PROFILE)


def load_profile() -> dict[str, Any]:
    stored = read_json(PROFILE_FILE, default=None)
    if not stored:
        return default_profile()
    return _merge(default_profile(), stored)


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_profile(profile)
    write_json(PROFILE_FILE, normalized)
    return normalized


def reset_profile() -> dict[str, Any]:
    return save_profile(default_profile())


def update_profile(patch: dict[str, Any], replace_lists: bool = False) -> dict[str, Any]:
    """Apply a partial update and persist it.

    ``replace_lists`` controls list semantics: the default merges (adds new
    entries, keeps existing ones) which is what "also avoid polyester" means;
    passing True replaces outright, which is what "my colours are black and
    white only" means. Both readings are common, so the caller must choose.
    """
    current = load_profile()
    merged = _merge(current, patch, replace_lists=replace_lists)
    return save_profile(merged)


def _merge(base: dict[str, Any], patch: dict[str, Any], replace_lists: bool = True) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if key not in out and key not in _SECTIONS:
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value, replace_lists=replace_lists)
        elif isinstance(value, list) and isinstance(out.get(key), list) and not replace_lists:
            merged = list(out[key])
            merged.extend(v for v in value if v not in merged)
            out[key] = merged
        else:
            out[key] = copy.deepcopy(value)
    return out


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Clamp values into sane ranges and re-normalise the scoring weights."""
    prof = _merge(default_profile(), profile)

    commercial = prof["commercial"]
    commercial["max_moq"] = max(1, int(commercial.get("max_moq") or 1))
    commercial["ideal_order_quantity"] = max(1, int(commercial.get("ideal_order_quantity") or 1))
    for field in ("target_unit_price_cny", "max_unit_price_cny", "min_unit_price_cny", "budget_total_cny"):
        commercial[field] = max(0.0, float(commercial.get(field) or 0.0))
    if commercial["max_unit_price_cny"] and commercial["max_unit_price_cny"] < commercial["target_unit_price_cny"]:
        # A ceiling below the target is always a typo; trust the explicit ceiling
        # and pull the target down to meet it rather than silently filtering out
        # every offer the buyer asked for.
        commercial["target_unit_price_cny"] = commercial["max_unit_price_cny"]

    supplier = prof["supplier"]
    for field, lo, hi in (
        ("min_repurchase_rate", 0.0, 1.0),
        ("min_response_rate", 0.0, 1.0),
        ("max_refund_rate", 0.0, 1.0),
    ):
        supplier[field] = min(hi, max(lo, float(supplier.get(field) or 0.0)))
    supplier["min_composite_rating"] = min(5.0, max(0.0, float(supplier.get("min_composite_rating") or 0.0)))
    supplier["min_years_on_platform"] = max(0.0, float(supplier.get("min_years_on_platform") or 0.0))

    materials = prof["materials"]
    materials["min_natural_fiber_pct"] = min(100, max(0, int(materials.get("min_natural_fiber_pct") or 0)))
    for field in ("min_gsm", "max_gsm"):
        materials[field] = max(0, int(materials.get(field) or 0))

    prof["weights"] = normalize_weights(prof.get("weights") or {})
    return prof


def normalize_weights(weights: dict[str, Any]) -> dict[str, float]:
    """Scale weights to sum to 1 so scores stay comparable across profiles."""
    known = DEFAULT_PROFILE["weights"]
    cleaned = {k: max(0.0, float(weights.get(k, known[k]) or 0.0)) for k in known}
    total = sum(cleaned.values())
    if total <= 0:
        return dict(known)
    scaled = {k: round(v / total, 6) for k, v in cleaned.items()}
    # Rounding each weight independently leaves a residual, so the weights stop
    # summing to exactly 1 and scores drift out of the 0-1 range they are
    # presented in. Absorb the residual into the largest weight.
    residual = round(1.0 - sum(scaled.values()), 6)
    if residual:
        heaviest = max(scaled, key=lambda k: scaled[k])
        scaled[heaviest] = round(scaled[heaviest] + residual, 6)
    return scaled


def summarize(profile: dict[str, Any]) -> str:
    """One-screen human summary - what the connecting AI reads back to the user."""
    ident, style, comm = profile["identity"], profile["style"], profile["commercial"]
    sup, mat, log = profile["supplier"], profile["materials"], profile["logistics"]
    lines = [
        f"Buyer: {ident['buyer_type']} shipping to {ident['destination_country']} ({ident['currency']})",
        f"Categories: {', '.join(style['categories']) or 'any'} | audience: {', '.join(style['audience']) or 'any'}",
        f"Aesthetic: {', '.join(style['aesthetic']) or 'unset'} | colours: {', '.join(style['preferred_colors']) or 'any'}",
        f"Price: target ¥{comm['target_unit_price_cny']:.2f}/unit, ceiling ¥{comm['max_unit_price_cny']:.2f}, "
        f"floor ¥{comm['min_unit_price_cny']:.2f}",
        f"Quantity: MOQ ceiling {comm['max_moq']}, planned order {comm['ideal_order_quantity']} units, "
        f"budget ¥{comm['budget_total_cny']:.0f}",
        f"Materials: require {mat['required_any'] or 'any'}, ban {mat['banned'] or 'none'}, "
        f"min natural fibre {mat['min_natural_fiber_pct']}%",
        f"Supplier floor: {sup['min_years_on_platform']}y on platform, repurchase >= {sup['min_repurchase_rate']:.0%}, "
        f"rating >= {sup['min_composite_rating']}, TrustPass {'required' if sup['require_trustpass'] else 'optional'}",
        f"Logistics: {log['shipping_mode']}, lead time <= {log['lead_time_days_max']} days",
        "Weights: " + ", ".join(f"{k} {v:.0%}" for k, v in profile["weights"].items()),
    ]
    if profile.get("notes"):
        lines.append(f"Notes: {profile['notes']}")
    return "\n".join(lines)
