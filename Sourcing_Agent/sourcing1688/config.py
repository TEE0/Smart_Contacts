"""Runtime configuration for the 1688 sourcing agent.

Everything is environment driven so the same package can run as a local stdio
MCP server, a remote streamable-HTTP connector, or a plain CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def home_dir() -> Path:
    """Directory holding the preference profile and saved shortlists."""
    raw = _env("SOURCING_HOME")
    path = Path(raw).expanduser() if raw else Path.home() / ".config" / "sourcing1688"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class OfficialProviderConfig:
    """Credentials for the official 1688 / Alibaba open platform.

    The open platform signs every call with an HMAC over the request path plus
    the sorted parameters. API *namespaces* are granted per application, so the
    concrete namespace/method for each operation is configurable rather than
    hardcoded - what your app key is allowed to call differs between accounts.
    """

    base_url: str = field(default_factory=lambda: _env("SOURCING_1688_BASE_URL", "https://gw.open.1688.com/openapi"))
    app_key: str = field(default_factory=lambda: _env("SOURCING_1688_APP_KEY"))
    app_secret: str = field(default_factory=lambda: _env("SOURCING_1688_APP_SECRET"))
    access_token: str = field(default_factory=lambda: _env("SOURCING_1688_ACCESS_TOKEN"))
    protocol_version: str = field(default_factory=lambda: _env("SOURCING_1688_PROTOCOL", "param2/1"))
    signature_method: str = field(default_factory=lambda: _env("SOURCING_1688_SIGN_METHOD", "hmac-sha1"))
    search_api: str = field(
        default_factory=lambda: _env(
            "SOURCING_1688_SEARCH_API", "com.alibaba.fenxiao.crossborder:product.search.offerSearch"
        )
    )
    detail_api: str = field(
        default_factory=lambda: _env(
            "SOURCING_1688_DETAIL_API", "com.alibaba.fenxiao.crossborder:product.search.queryProductDetail"
        )
    )
    image_search_api: str = field(
        default_factory=lambda: _env(
            "SOURCING_1688_IMAGE_API", "com.alibaba.fenxiao.crossborder:product.image.search"
        )
    )

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)


@dataclass
class AggregatorProviderConfig:
    """Generic adapter for a third-party 1688 data API (RapidAPI/OTAPI style).

    These vendors all expose "search / detail / image search" over plain REST
    with different field names, so the mapping is configuration, not code.
    """

    base_url: str = field(default_factory=lambda: _env("SOURCING_AGG_BASE_URL"))
    api_key: str = field(default_factory=lambda: _env("SOURCING_AGG_API_KEY"))
    api_key_header: str = field(default_factory=lambda: _env("SOURCING_AGG_KEY_HEADER", "x-api-key"))
    extra_headers: str = field(default_factory=lambda: _env("SOURCING_AGG_EXTRA_HEADERS"))
    search_path: str = field(default_factory=lambda: _env("SOURCING_AGG_SEARCH_PATH", "/search"))
    detail_path: str = field(default_factory=lambda: _env("SOURCING_AGG_DETAIL_PATH", "/detail"))
    image_search_path: str = field(default_factory=lambda: _env("SOURCING_AGG_IMAGE_PATH", "/image-search"))
    query_param: str = field(default_factory=lambda: _env("SOURCING_AGG_QUERY_PARAM", "keyword"))
    page_param: str = field(default_factory=lambda: _env("SOURCING_AGG_PAGE_PARAM", "page"))
    offer_id_param: str = field(default_factory=lambda: _env("SOURCING_AGG_OFFER_PARAM", "offer_id"))
    results_path: str = field(default_factory=lambda: _env("SOURCING_AGG_RESULTS_PATH", "result.items"))

    @property
    def configured(self) -> bool:
        return bool(self.base_url)


@dataclass
class CostingConfig:
    """Default landed-cost assumptions. Overridable per call and per profile."""

    fx_cny_per_unit: float = field(default_factory=lambda: _env_float("SOURCING_FX_CNY_PER_UNIT", 7.15))
    currency: str = field(default_factory=lambda: _env("SOURCING_CURRENCY", "USD"))
    domestic_freight_cny: float = field(default_factory=lambda: _env_float("SOURCING_DOMESTIC_FREIGHT_CNY", 15.0))
    agent_fee_pct: float = field(default_factory=lambda: _env_float("SOURCING_AGENT_FEE_PCT", 5.0))
    air_rate_per_kg: float = field(default_factory=lambda: _env_float("SOURCING_AIR_RATE_PER_KG", 7.5))
    sea_rate_per_kg: float = field(default_factory=lambda: _env_float("SOURCING_SEA_RATE_PER_KG", 2.2))
    express_rate_per_kg: float = field(default_factory=lambda: _env_float("SOURCING_EXPRESS_RATE_PER_KG", 11.0))
    volumetric_divisor: int = field(default_factory=lambda: _env_int("SOURCING_VOLUMETRIC_DIVISOR", 6000))
    duty_pct: float = field(default_factory=lambda: _env_float("SOURCING_DUTY_PCT", 12.0))
    import_tax_pct: float = field(default_factory=lambda: _env_float("SOURCING_IMPORT_TAX_PCT", 20.0))
    qc_fee_per_order: float = field(default_factory=lambda: _env_float("SOURCING_QC_FEE", 30.0))


@dataclass
class Settings:
    provider: str = field(default_factory=lambda: _env("SOURCING_PROVIDER", "mock").lower())
    request_timeout: float = field(default_factory=lambda: _env_float("SOURCING_HTTP_TIMEOUT", 20.0))
    max_results_per_query: int = field(default_factory=lambda: _env_int("SOURCING_MAX_RESULTS", 40))
    detail_fetch_limit: int = field(default_factory=lambda: _env_int("SOURCING_DETAIL_LIMIT", 12))
    read_only: bool = field(default_factory=lambda: _env_bool("SOURCING_READ_ONLY", False))
    http_bearer_token: str = field(default_factory=lambda: _env("SOURCING_HTTP_BEARER_TOKEN"))
    official: OfficialProviderConfig = field(default_factory=OfficialProviderConfig)
    aggregator: AggregatorProviderConfig = field(default_factory=AggregatorProviderConfig)
    costing: CostingConfig = field(default_factory=CostingConfig)

    @classmethod
    def load(cls) -> "Settings":
        return cls()


def get_settings(refresh: bool = False) -> Settings:
    global _SETTINGS
    if refresh or _SETTINGS is None:
        _SETTINGS = Settings.load()
    return _SETTINGS


_SETTINGS: Settings | None = None
