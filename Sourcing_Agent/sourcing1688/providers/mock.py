"""Offline provider backed by a hand-built fixture catalogue.

This exists so the whole pipeline - query planning, filtering, scoring, supplier
vetting, landed cost, negotiation drafts - can be exercised, tested and
demonstrated before anyone pays for 1688 data access. The fixtures are written
to include the traps a real search returns: a polyester listing that looks cheap
until a natural-fibre floor is applied, a 0.5-year-old reseller with no
TrustPass, and a "clearance / A货" listing that a counterfeit filter must drop.

It is the default provider precisely because a shopping agent that silently
returns nothing is worse than one that is obviously running on samples.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..lexicon import has_chinese, translate_phrase
from ..models import Offer
from .mapping import build_offer

_FIXTURES = Path(__file__).resolve().parent.parent / "data" / "mock_offers.json"


@lru_cache(maxsize=1)
def _catalogue() -> list[dict[str, Any]]:
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


def _haystack(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("subject", "")),
        str(item.get("subjectTrans", "")),
        str(item.get("categoryName", "")),
        " ".join(f"{k}{v}" for k, v in (item.get("attributes") or {}).items()),
        str((item.get("sellerInfo") or {}).get("companyName", "")),
    ]
    return " ".join(parts).lower()


def _match_score(item: dict[str, Any], query: str) -> int:
    """Count how many query tokens appear in the listing.

    Chinese has no spaces, so the query is matched by sliding n-grams - the same
    substring behaviour 1688's own keyword search exhibits.
    """
    hay = _haystack(item)
    query = (query or "").strip().lower()
    if not query:
        return 0
    if has_chinese(query):
        hits = 0
        for size in (4, 3, 2):
            for i in range(len(query) - size + 1):
                gram = query[i : i + size]
                if gram.strip() and gram in hay:
                    hits += size
        return hits
    return sum(2 for token in query.split() if token and token in hay)


class MockProvider:
    name = "mock"

    def search(self, query: str, *, page: int = 1, limit: int = 40, filters: dict[str, Any] | None = None) -> list[Offer]:
        filters = filters or {}
        zh_query = query if has_chinese(query) else (translate_phrase(query) or query)

        scored: list[tuple[int, dict[str, Any]]] = []
        for item in _catalogue():
            score = _match_score(item, zh_query) + _match_score(item, query)
            if score:
                scored.append((score, item))

        if not scored:
            # An empty result from a sample catalogue reads as "nothing exists on
            # 1688", which is misleading. Return the catalogue so the downstream
            # filters still demonstrate themselves, ordered by popularity.
            scored = [(0, item) for item in _catalogue()]

        scored.sort(key=lambda pair: (-pair[0], -int(pair[1].get("monthSold") or 0)))

        offers: list[Offer] = []
        for _score, item in scored:
            offer = build_offer(item, zh_query, self.name)
            if filters.get("price_start") is not None and offer.price_cny < float(filters["price_start"]):
                continue
            if filters.get("price_end") is not None and offer.price_cny > float(filters["price_end"]):
                continue
            offers.append(offer)

        start = max(0, (page - 1) * limit)
        return offers[start : start + limit]

    def get_offer(self, offer_id: str) -> Offer | None:
        for item in _catalogue():
            if str(item.get("offerId")) == str(offer_id):
                return build_offer(item, "", self.name)
        return None

    def search_by_image(self, image_url: str, *, limit: int = 20) -> list[Offer]:
        # Without a real vision backend the honest behaviour is to say so rather
        # than return arbitrary listings dressed up as visual matches.
        offers = [build_offer(item, f"image:{image_url}", self.name) for item in _catalogue()[:limit]]
        for offer in offers:
            offer.attributes = {**offer.attributes, "_mock_notice": "fixture data, not a visual match"}
        return offers

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": True,
            "offers": len(_catalogue()),
            "note": "Fixture catalogue. Set SOURCING_PROVIDER=official or aggregator for live 1688 data.",
        }
