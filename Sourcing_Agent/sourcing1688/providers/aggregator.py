"""Generic adapter for third-party 1688 data APIs.

Licensed resellers of 1688 data (OTAPI, the RapidAPI vendors, and similar) all
expose the same three operations over ordinary REST with different paths, auth
headers and envelope shapes. Those differences are configuration here, not code,
so switching vendor is an environment change rather than a pull request.

This is usually the pragmatic starting point: an open-platform application takes
time to approve, whereas an aggregator key works the same afternoon.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Settings, get_settings
from ..http_client import HttpError, dig, request_json
from ..models import Offer
from .base import ProviderError, ProviderNotConfigured
from .mapping import build_offer, find_items


class AggregatorProvider:
    name = "aggregator"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.cfg = self.settings.aggregator

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.cfg.api_key:
            headers[self.cfg.api_key_header] = self.cfg.api_key
        if self.cfg.extra_headers:
            try:
                extra = json.loads(self.cfg.extra_headers)
                if isinstance(extra, dict):
                    headers.update({str(k): str(v) for k, v in extra.items()})
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "SOURCING_AGG_EXTRA_HEADERS must be a JSON object, "
                    'e.g. {"x-rapidapi-host": "example.p.rapidapi.com"}'
                ) from exc
        return headers

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.cfg.configured:
            raise ProviderNotConfigured(
                "The aggregator provider needs SOURCING_AGG_BASE_URL (and usually SOURCING_AGG_API_KEY). "
                "Set SOURCING_PROVIDER=mock to trial the workflow without a vendor key."
            )
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            return request_json(url, params=params, headers=self._headers(), timeout=self.settings.request_timeout)
        except HttpError as exc:
            if exc.status in (401, 403):
                raise ProviderError(f"Aggregator rejected the API key ({exc.status}). Check SOURCING_AGG_API_KEY.") from exc
            if exc.status == 429:
                raise ProviderError("Aggregator rate limit hit (429). Slow the search fan-out or upgrade the plan.") from exc
            raise ProviderError(f"Aggregator call failed: {exc}") from exc

    def _extract(self, payload: Any) -> list[dict[str, Any]]:
        """Prefer the configured results path, fall back to structural search."""
        located = dig(payload, self.cfg.results_path)
        if isinstance(located, list) and located:
            return [item for item in located if isinstance(item, dict)]
        return find_items(payload)

    def search(self, query: str, *, page: int = 1, limit: int = 40, filters: dict[str, Any] | None = None) -> list[Offer]:
        filters = filters or {}
        params: dict[str, Any] = {
            self.cfg.query_param: query,
            self.cfg.page_param: page,
            "page_size": min(limit, self.settings.max_results_per_query),
        }
        for key in ("price_start", "price_end", "sort", "category_id"):
            if filters.get(key) is not None:
                params[key] = filters[key]
        payload = self._get(self.cfg.search_path, params)
        return [build_offer(item, query, self.name) for item in self._extract(payload)]

    def get_offer(self, offer_id: str) -> Offer | None:
        payload = self._get(self.cfg.detail_path, {self.cfg.offer_id_param: offer_id})
        items = self._extract(payload)
        return build_offer(items[0], "", self.name) if items else None

    def search_by_image(self, image_url: str, *, limit: int = 20) -> list[Offer]:
        payload = self._get(self.cfg.image_search_path, {"image_url": image_url, "page_size": limit})
        return [build_offer(item, f"image:{image_url}", self.name) for item in self._extract(payload)]

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": self.cfg.configured,
            "base_url": self.cfg.base_url or "(unset)",
            "has_api_key": bool(self.cfg.api_key),
            "results_path": self.cfg.results_path,
            "note": "Field mapping is key-hint driven; set SOURCING_AGG_RESULTS_PATH if the vendor nests results unusually.",
        }
