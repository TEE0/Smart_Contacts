"""Normalized domain models.

Every provider maps its own payload into these shapes, so the planner, scorer,
vetting rules and costing model never see vendor-specific field names.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


@dataclass
class PriceTier:
    """One row of a 1688 quantity ladder (阶梯价)."""

    min_quantity: int
    price_cny: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Sku:
    """A concrete buyable variant (colour / size combination)."""

    sku_id: str = ""
    color: str = ""
    size: str = ""
    price_cny: float = 0.0
    stock: int = 0
    attributes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Supplier:
    """Seller-side signals used for vetting and trust scoring."""

    seller_id: str = ""
    name: str = ""
    shop_url: str = ""
    province: str = ""
    city: str = ""
    years_on_platform: float = 0.0
    is_verified_factory: bool = False          # 实力商家 / 验厂
    is_trustpass: bool = False                 # 诚信通
    repurchase_rate: float = 0.0               # 复购率 0-1
    response_rate: float = 0.0                 # 响应率 0-1
    avg_response_minutes: float | None = None
    refund_rate: float | None = None           # 退款率 0-1, lower is better
    dispute_rate: float | None = None
    composite_rating: float = 0.0              # 0-5 platform score
    transactions_90d: int = 0
    supports_sample: bool = False
    supports_customization: bool = False       # OEM/ODM, 定制
    business_type: str = ""                    # factory | trader | unknown

    def as_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Offer:
    """A normalized 1688 product listing."""

    offer_id: str
    title: str = ""
    title_en: str = ""
    url: str = ""
    image_url: str = ""
    images: list[str] = field(default_factory=list)
    price_cny: float = 0.0                     # headline / lowest advertised
    price_tiers: list[PriceTier] = field(default_factory=list)
    moq: int = 1
    unit: str = "piece"
    sold_30d: int = 0
    sold_total: int = 0
    review_count: int = 0
    rating: float = 0.0
    category_path: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
    material_text: str = ""
    skus: list[Sku] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    sizes: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    unit_weight_kg: float = 0.0
    carton_dims_cm: tuple[float, float, float] | None = None
    supplier: Supplier = field(default_factory=Supplier)
    source_query: str = ""
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def price_at_quantity(self, quantity: int) -> float:
        """Cheapest tier price that the given order quantity unlocks."""
        if not self.price_tiers:
            return self.price_cny
        eligible = [t for t in self.price_tiers if quantity >= t.min_quantity]
        if not eligible:
            return min(t.price_cny for t in self.price_tiers)
        return min(t.price_cny for t in eligible)

    def searchable_text(self) -> str:
        parts = [self.title, self.title_en, self.material_text, " ".join(self.category_path)]
        parts.extend(f"{k} {v}" for k, v in self.attributes.items())
        return " ".join(p for p in parts if p).lower()

    def as_dict(self, include_raw: bool = False) -> dict[str, Any]:
        data = {
            "offer_id": self.offer_id,
            "title": self.title,
            "title_en": self.title_en,
            "url": self.url,
            "image_url": self.image_url,
            "price_cny": self.price_cny,
            "price_tiers": [t.as_dict() for t in self.price_tiers],
            "moq": self.moq,
            "unit": self.unit,
            "sold_30d": self.sold_30d,
            "sold_total": self.sold_total,
            "review_count": self.review_count,
            "rating": self.rating,
            "category_path": self.category_path,
            "attributes": self.attributes,
            "material_text": self.material_text,
            "colors": self.colors,
            "sizes": self.sizes,
            "certifications": self.certifications,
            "unit_weight_kg": self.unit_weight_kg,
            "supplier": self.supplier.as_dict(),
            "source_query": self.source_query,
            "provider": self.provider,
        }
        if self.skus:
            data["skus"] = [s.as_dict() for s in self.skus]
        if include_raw:
            data["raw"] = self.raw
        return _clean(data)


@dataclass
class ScoreFactor:
    name: str
    weight: float
    raw_score: float          # 0-1 before weighting
    contribution: float       # weight * raw_score
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": round(self.weight, 4),
            "score": round(self.raw_score, 4),
            "contribution": round(self.contribution, 4),
            "detail": self.detail,
        }


@dataclass
class ScoredOffer:
    offer: Offer
    score: float
    factors: list[ScoreFactor] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    landed_cost: dict[str, Any] | None = None
    rejected_reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.rejected_reasons

    def as_dict(self, include_offer: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "offer_id": self.offer.offer_id,
            "score": round(self.score, 4),
            "factors": [f.as_dict() for f in self.factors],
            "risk_flags": self.risk_flags,
            "strengths": self.strengths,
            "rejected_reasons": self.rejected_reasons,
        }
        if include_offer:
            data["offer"] = self.offer.as_dict()
        if self.landed_cost:
            data["landed_cost"] = self.landed_cost
        return data


@dataclass
class SearchPlan:
    """One concrete 1688 query derived from an English brief + preferences."""

    query_zh: str
    query_en: str
    rationale: str
    intent: str = "primary"      # primary | variant | material | fallback
    filters: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
