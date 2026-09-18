"""Shared payload -> :class:`~sourcing1688.models.Offer` mapping.

Both real providers face the same problem: every 1688 data source names its
fields differently (``subject`` vs ``title`` vs ``productName``, ``offerId`` vs
``productId`` vs ``num_iid``) and buries the result list at a different depth.
Rather than one brittle parser per vendor, the mapping is key-hint driven and
case/underscore insensitive, so a new vendor usually needs no code at all.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import Offer, PriceTier, Sku, Supplier

PRODUCT_KEY_HINTS = ("offerid", "productid", "itemid", "numiid", "id")
TITLE_KEY_HINTS = ("subject", "title", "name", "productname")


def build_offer(item: dict[str, Any], query: str, provider_name: str) -> Offer:
    get = _getter(item)
    offer_id = str(get(*PRODUCT_KEY_HINTS) or "")
    tiers = _parse_tiers(item)
    price = _first_float(get("price", "promotionprice", "sellprice", "referenceprice"))
    if not price and tiers:
        price = min(t.price_cny for t in tiers)

    offer = Offer(
        offer_id=offer_id,
        title=str(get(*TITLE_KEY_HINTS) or ""),
        title_en=str(get("subjecttrans", "titleen", "englishtitle") or ""),
        url=str(get("detailurl", "offerurl", "producturl") or (f"https://detail.1688.com/offer/{offer_id}.html" if offer_id else "")),
        image_url=_first_image(item),
        images=_all_images(item),
        price_cny=price,
        price_tiers=tiers,
        moq=int(_first_float(get("minorderquantity", "moq", "beginamount")) or 1),
        unit=str(get("unit", "saleunit") or "piece"),
        sold_30d=int(_first_float(get("monthsold", "saleinfo", "soldout30days")) or 0),
        sold_total=int(_first_float(get("totalsold", "soldquantity")) or 0),
        review_count=int(_first_float(get("evaluatecount", "reviewcount")) or 0),
        rating=_first_float(get("score", "starlevel", "rating")),
        category_path=_as_list(get("categorypath", "categoryname")),
        attributes=_parse_attributes(item),
        skus=_parse_skus(item),
        supplier=build_supplier(item),
        source_query=query,
        provider=provider_name,
        raw=item,
    )
    offer.material_text = offer.attributes.get("材质", "") or offer.attributes.get("面料名称", "")
    offer.colors = _as_list(get("colors")) or sorted({s.color for s in offer.skus if s.color})
    offer.sizes = _as_list(get("sizes")) or sorted({s.size for s in offer.skus if s.size})
    return offer

def build_supplier(item: dict[str, Any]) -> Supplier:
    blob = item.get("sellerInfo") or item.get("supplierInfo") or item.get("companyInfo") or item
    get = _getter(blob)
    return Supplier(
        seller_id=str(get("sellerid", "memberid", "loginid", "userid") or ""),
        name=str(get("companyname", "shopname", "sellernick", "loginid") or ""),
        shop_url=str(get("shopurl", "winportUrl", "winporturl") or ""),
        province=str(get("province", "provincename") or ""),
        city=str(get("city", "cityname") or ""),
        years_on_platform=_first_float(get("tpyear", "bizyear", "yearsonplatform", "memberyear")),
        is_verified_factory=_truthy(get("ispowerfulmerchant", "isverifiedfactory", "certifiedfactory")),
        is_trustpass=_truthy(get("istrustpass", "ischengxintong", "tp")),
        repurchase_rate=_rate(get("repurchaserate", "回头率", "repeatpurchaserate")),
        response_rate=_rate(get("responserate", "replyrate")),
        composite_rating=_first_float(get("compositescore", "sellerrating", "score")),
        transactions_90d=int(_first_float(get("tradecount", "salecount")) or 0),
        supports_sample=_truthy(get("supportsample", "mixwholesale")),
        supports_customization=_truthy(get("supportcustomization", "isoem", "processingcustomization")),
        business_type=str(get("businesstype", "companytype") or ""),
    )


# --------------------------------------------------------------------- helpers


def _getter(item: dict[str, Any]):
    """Case/underscore-insensitive lookup across candidate key names."""
    flat = {str(k).replace("_", "").lower(): v for k, v in (item or {}).items()}

    def get(*names: str) -> Any:
        for name in names:
            value = flat.get(name.replace("_", "").lower())
            if value not in (None, "", [], {}):
                return value
        return None

    return get


def looks_like_product(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    get = _getter(item)
    return bool(get(*PRODUCT_KEY_HINTS)) and bool(get(*TITLE_KEY_HINTS, "price"))


def find_items(payload: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Locate the first list of product-shaped dicts anywhere in the envelope."""
    if depth > 6:
        return []
    if isinstance(payload, list):
        products = [p for p in payload if looks_like_product(p)]
        if products:
            return products
        for entry in payload:
            found = find_items(entry, depth + 1)
            if found:
                return found
        return []
    if isinstance(payload, dict):
        for key in ("result", "data", "items", "offerList", "productList", "list", "records"):
            if key in payload:
                found = find_items(payload[key], depth + 1)
                if found:
                    return found
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = find_items(value, depth + 1)
                if found:
                    return found
    return []


def _first_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for candidate in value.values():
            parsed = _first_float(candidate)
            if parsed:
                return parsed
        return 0.0
    if isinstance(value, list):
        return _first_float(value[0]) if value else 0.0

    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else 0.0


def _rate(value: Any) -> float:
    """Normalise a percentage-ish field to 0-1."""
    number = _first_float(value)
    if number > 1.0:
        return min(number / 100.0, 1.0)
    return max(0.0, number)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0"):
        return False
    return str(value).strip().lower() not in {"false", "no", "none", "0"}


def _as_list(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v not in (None, "")]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v not in (None, "")]
    return [part for part in str(value).replace("/", ">").split(">") if part.strip()]


def _first_image(item: dict[str, Any]) -> str:
    images = _all_images(item)
    return images[0] if images else ""


def _all_images(item: dict[str, Any]) -> list[str]:
    get = _getter(item)
    raw = get("imageurl", "images", "imagelist", "mainimage", "picurl", "image")
    if isinstance(raw, dict):
        raw = raw.get("images") or list(raw.values())
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        out: list[str] = []
        for entry in raw:
            if isinstance(entry, str):
                out.append(entry)
            elif isinstance(entry, dict):
                url = _getter(entry)("url", "imageurl", "fullpathimageurl")
                if url:
                    out.append(str(url))
        return out
    return []


def _parse_tiers(item: dict[str, Any]) -> list[PriceTier]:
    raw = _getter(item)("pricerangelist", "priceranges", "quantityprices", "skupriceranges", "ladderprice")
    tiers: list[PriceTier] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            get = _getter(entry)
            qty = int(_first_float(get("startquantity", "beginamount", "minquantity", "min")) or 1)
            price = _first_float(get("price", "unitprice", "value"))
            if price:
                tiers.append(PriceTier(min_quantity=qty, price_cny=price))
    tiers.sort(key=lambda t: t.min_quantity)
    return tiers


def _parse_attributes(item: dict[str, Any]) -> dict[str, str]:
    raw = _getter(item)("attributes", "productattribute", "attrs", "props")
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v not in (None, "")}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                get = _getter(entry)
                name = get("attributename", "name", "key", "propertyname")
                value = get("value", "attributevalue", "propertyvalue")
                if name and value:
                    out[str(name)] = str(value)
    return out


def _parse_skus(item: dict[str, Any]) -> list[Sku]:
    raw = _getter(item)("skuinfos", "skus", "skulist", "productskuinfos")
    out: list[Sku] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        get = _getter(entry)
        attrs: dict[str, str] = {}
        for attr in entry.get("attributes") or entry.get("skuAttributes") or []:
            if isinstance(attr, dict):
                aget = _getter(attr)
                name = aget("attributename", "name", "key")
                value = aget("value", "attributevalue")
                if name and value:
                    attrs[str(name)] = str(value)
        out.append(
            Sku(
                sku_id=str(get("skuid", "id", "specid") or ""),
                color=str(attrs.get("颜色") or attrs.get("Color") or get("color") or ""),
                size=str(attrs.get("尺码") or attrs.get("Size") or get("size") or ""),
                price_cny=_first_float(get("price", "consignprice")),
                stock=int(_first_float(get("amountonsale", "stock", "quantity")) or 0),
                attributes=attrs,
            )
        )
    return out
