"""Provider contract.

1688 has no single public, universally available API. Realistically a buyer
reaches it through one of three routes, and which one they can use depends on
paperwork, not on code:

1. The official Alibaba/1688 open platform (app key + secret + OAuth token).
   Real data, but API namespaces are granted per application and most overseas
   accounts only get a cross-border subset.
2. A licensed third-party data API (OTAPI, RapidAPI vendors, and similar).
   Instant access, per-call cost, varying field coverage.
3. Nothing yet - evaluating the workflow before paying for access.

So the agent programs against this interface and picks an implementation at
runtime. Every piece of intelligence in this package (query planning, filtering,
scoring, vetting, costing) works identically across all three.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..models import Offer


class ProviderError(RuntimeError):
    """Raised when an upstream call fails in a way the caller should see."""


class ProviderNotConfigured(ProviderError):
    """Raised when credentials for the selected provider are missing."""


@runtime_checkable
class SourcingProvider(Protocol):
    name: str

    def search(self, query: str, *, page: int = 1, limit: int = 40, filters: dict[str, Any] | None = None) -> list[Offer]:
        """Keyword search. ``query`` is already in Chinese where possible."""

    def get_offer(self, offer_id: str) -> Offer | None:
        """Full detail for one listing, including SKUs and supplier signals."""

    def search_by_image(self, image_url: str, *, limit: int = 20) -> list[Offer]:
        """Reverse image search - the 1688 workflow buyers actually use."""

    def health(self) -> dict[str, Any]:
        """Report configuration state without making a paid call."""


def dedupe_offers(batches: list[list[Offer]]) -> list[Offer]:
    """Flatten multi-query results, keeping the first sighting of each offer.

    Fan-out across synonym queries is the point of the planner, and the same
    listing routinely appears under three of them - deduping here keeps the
    ranked output honest about how many distinct suppliers were actually found.
    """
    seen: dict[str, Offer] = {}
    for batch in batches:
        for offer in batch:
            if not offer.offer_id:
                continue
            existing = seen.get(offer.offer_id)
            if existing is None:
                seen[offer.offer_id] = offer
            elif offer.source_query and offer.source_query not in existing.source_query:
                existing.source_query = f"{existing.source_query}; {offer.source_query}".strip("; ")
    return list(seen.values())
