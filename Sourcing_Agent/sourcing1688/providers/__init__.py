"""Provider registry - selects the 1688 access route at runtime."""

from __future__ import annotations

from ..config import Settings, get_settings
from .base import ProviderError, ProviderNotConfigured, SourcingProvider, dedupe_offers

__all__ = [
    "ProviderError",
    "ProviderNotConfigured",
    "SourcingProvider",
    "dedupe_offers",
    "get_provider",
    "available_providers",
]


def available_providers() -> list[str]:
    return ["mock", "official", "aggregator"]


def get_provider(name: str | None = None, settings: Settings | None = None) -> SourcingProvider:
    settings = settings or get_settings()
    choice = (name or settings.provider or "mock").lower()

    if choice == "mock":
        from .mock import MockProvider

        return MockProvider()
    if choice in {"official", "1688", "open1688"}:
        from .official import OfficialProvider

        return OfficialProvider(settings)
    if choice in {"aggregator", "rapidapi", "otapi", "thirdparty"}:
        from .aggregator import AggregatorProvider

        return AggregatorProvider(settings)

    raise ProviderError(
        f"Unknown provider {choice!r}. Set SOURCING_PROVIDER to one of: {', '.join(available_providers())}."
    )
