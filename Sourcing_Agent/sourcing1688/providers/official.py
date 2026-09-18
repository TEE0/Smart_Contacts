"""Official 1688 / Alibaba open platform adapter.

Calls go to ``{base}/{protocol}/{namespace}/{apiName}/{appKey}`` and carry an
``_aop_signature`` computed over the request path plus every parameter sorted by
name. The platform accepts HMAC-SHA1 (the long-standing default) and HMAC-SHA256
on newer namespaces, so the algorithm is configurable.

Two things about this adapter are intentional:

* The namespace and method for each operation are **configuration**, not
  constants. Open-platform permissions are granted per application - a
  cross-border account and a domestic account call different methods for the
  same job, and hardcoding either would break the other.
* Response parsing is defensive. Namespaces differ in envelope shape, so the
  adapter searches for the first list of product-shaped objects rather than
  assuming one key.

``access_token`` is short-lived (the platform issues hour-scale tokens). This
adapter reads it from configuration and reports expiry errors clearly instead of
silently returning nothing - refresh belongs in whatever holds your OAuth
credentials, not in a shopping agent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from ..config import Settings, get_settings
from ..http_client import HttpError, request_json
from ..models import Offer
from .base import ProviderError, ProviderNotConfigured
from .mapping import build_offer, find_items, looks_like_product


def sign_request(path: str, params: dict[str, Any], app_secret: str, method: str = "hmac-sha1") -> str:
    """Compute the open platform ``_aop_signature``.

    The signed string is the API path (without the host or the ``/openapi``
    prefix) followed by every parameter concatenated as ``key + value`` in
    ascending key order. The result is upper-case hex.
    """
    ordered = "".join(f"{k}{_stringify(params[k])}" for k in sorted(params) if k != "_aop_signature")
    payload = f"{path}{ordered}".encode("utf-8")
    digest = hashlib.sha256 if method.lower().endswith("256") else hashlib.sha1
    return hmac.new(app_secret.encode("utf-8"), payload, digest).hexdigest().upper()


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return "" if value is None else str(value)


def _split_api(spec: str) -> tuple[str, str]:
    """Split a ``namespace:method`` configuration value."""
    if ":" in spec:
        namespace, method = spec.split(":", 1)
    elif "/" in spec:
        namespace, method = spec.rsplit("/", 1)
    else:
        raise ProviderError(
            f"API spec {spec!r} must be 'namespace:method', e.g. "
            "'com.alibaba.fenxiao.crossborder:product.search.offerSearch'."
        )
    return namespace.strip(), method.strip()


class OfficialProvider:
    name = "official"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.cfg = self.settings.official

    # ---------------------------------------------------------------- plumbing

    def _require_credentials(self) -> None:
        if not self.cfg.configured:
            raise ProviderNotConfigured(
                "The official 1688 provider needs SOURCING_1688_APP_KEY and SOURCING_1688_APP_SECRET. "
                "Register an application at open.1688.com, or set SOURCING_PROVIDER=mock to trial the "
                "workflow without credentials."
            )

    def _call(self, api_spec: str, params: dict[str, Any]) -> Any:
        self._require_credentials()
        namespace, method = _split_api(api_spec)
        path = f"{self.cfg.protocol_version}/{namespace}/{method}/{self.cfg.app_key}"
        url = f"{self.cfg.base_url.rstrip('/')}/{path}"

        body = {k: _stringify(v) for k, v in params.items() if v is not None}
        if self.cfg.access_token:
            body["access_token"] = self.cfg.access_token
        body["_aop_signature"] = sign_request(path, body, self.cfg.app_secret, self.cfg.signature_method)

        try:
            payload = request_json(url, method="POST", data=body, timeout=self.settings.request_timeout)
        except HttpError as exc:
            raise ProviderError(f"1688 open platform call {namespace}:{method} failed: {exc}") from exc

        if isinstance(payload, dict):
            error = payload.get("errorMessage") or payload.get("error_message") or payload.get("errorMsg")
            if error:
                code = payload.get("errorCode") or payload.get("error_code") or ""
                hint = ""
                if "token" in str(error).lower() or str(code).endswith("401"):
                    hint = " (access tokens are short-lived - refresh SOURCING_1688_ACCESS_TOKEN)"
                raise ProviderError(f"1688 open platform error {code}: {error}{hint}")
        return payload

    # ------------------------------------------------------------------ public

    def search(self, query: str, *, page: int = 1, limit: int = 40, filters: dict[str, Any] | None = None) -> list[Offer]:
        filters = filters or {}
        params: dict[str, Any] = {
            "keyword": query,
            "beginPage": page,
            "pageSize": min(limit, self.settings.max_results_per_query),
            "country": filters.get("country", "en"),
        }
        if filters.get("price_start") is not None:
            params["priceStart"] = filters["price_start"]
        if filters.get("price_end") is not None:
            params["priceEnd"] = filters["price_end"]
        if filters.get("category_id"):
            params["categoryId"] = filters["category_id"]
        if filters.get("sort"):
            params["sort"] = filters["sort"]

        payload = self._call(self.cfg.search_api, params)
        return [build_offer(item, query, self.name) for item in find_items(payload)]

    def get_offer(self, offer_id: str) -> Offer | None:
        payload = self._call(self.cfg.detail_api, {"offerId": offer_id, "productId": offer_id, "country": "en"})
        items = find_items(payload)
        if items:
            return build_offer(items[0], "", self.name)
        if isinstance(payload, dict):
            result = payload.get("result") or payload.get("data") or payload
            if isinstance(result, dict) and looks_like_product(result):
                return build_offer(result, "", self.name)
        return None

    def search_by_image(self, image_url: str, *, limit: int = 20) -> list[Offer]:
        payload = self._call(self.cfg.image_search_api, {"imageAddress": image_url, "pageSize": limit, "beginPage": 1})
        return [build_offer(item, f"image:{image_url}", self.name) for item in find_items(payload)]

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": self.cfg.configured,
            "has_access_token": bool(self.cfg.access_token),
            "base_url": self.cfg.base_url,
            "search_api": self.cfg.search_api,
            "detail_api": self.cfg.detail_api,
            "signature_method": self.cfg.signature_method,
            "note": (
                "Open platform namespaces are granted per application. If a call returns a permission "
                "error, set SOURCING_1688_SEARCH_API / _DETAIL_API to a namespace your app key is approved for."
            ),
        }
