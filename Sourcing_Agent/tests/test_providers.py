import hashlib
import hmac

import pytest

from sourcing1688.providers import ProviderError, available_providers, dedupe_offers, get_provider
from sourcing1688.providers.mapping import build_offer, find_items
from sourcing1688.providers.official import _split_api, sign_request


def test_registry_exposes_all_routes():
    assert set(available_providers()) == {"mock", "official", "aggregator"}


def test_unknown_provider_is_a_clear_error():
    with pytest.raises(ProviderError, match="Unknown provider"):
        get_provider("does-not-exist")


def test_official_provider_refuses_without_credentials(monkeypatch):
    monkeypatch.delenv("SOURCING_1688_APP_KEY", raising=False)
    monkeypatch.delenv("SOURCING_1688_APP_SECRET", raising=False)
    from sourcing1688.config import Settings
    from sourcing1688.providers.official import OfficialProvider

    provider = OfficialProvider(Settings.load())
    assert provider.health()["configured"] is False
    with pytest.raises(ProviderError, match="SOURCING_1688_APP_KEY"):
        provider.search("纯棉T恤")


# ------------------------------------------------------------- AOP signature


def test_signature_matches_the_documented_aop_scheme():
    path = "param2/1/com.alibaba.product/alibaba.product.get/APPKEY"
    params = {"productId": "123", "access_token": "tok"}
    expected = hmac.new(
        b"secret",
        f"{path}access_tokentokproductId123".encode(),
        hashlib.sha1,
    ).hexdigest().upper()
    assert sign_request(path, params, "secret") == expected


def test_signature_is_order_independent_and_excludes_itself():
    path = "param2/1/ns/method/KEY"
    a = sign_request(path, {"b": "2", "a": "1"}, "s")
    b = sign_request(path, {"a": "1", "b": "2", "_aop_signature": "stale"}, "s")
    assert a == b


def test_signature_algorithm_is_selectable():
    path = "param2/1/ns/method/KEY"
    assert sign_request(path, {"a": "1"}, "s", "hmac-sha256") != sign_request(path, {"a": "1"}, "s", "hmac-sha1")


@pytest.mark.parametrize("spec, expected", [
    ("com.alibaba.product:alibaba.product.get", ("com.alibaba.product", "alibaba.product.get")),
    ("com.alibaba.product/alibaba.product.get", ("com.alibaba.product", "alibaba.product.get")),
])
def test_api_spec_parsing(spec, expected):
    assert _split_api(spec) == expected


def test_malformed_api_spec_explains_the_format():
    with pytest.raises(ProviderError, match="namespace:method"):
        _split_api("nonsense")


# ----------------------------------------------------------------- mapping


def test_mapping_is_field_name_agnostic():
    # Vendors disagree on every key name; the mapper works off hints.
    a = build_offer({"offerId": "1", "subject": "T恤", "price": 10}, "q", "x")
    b = build_offer({"product_id": "1", "productName": "T恤", "sellPrice": "10.00"}, "q", "x")
    assert a.offer_id == b.offer_id == "1"
    assert a.title == b.title
    assert b.price_cny == 10.0


def test_results_are_found_at_any_envelope_depth():
    payload = {"result": {"data": {"offerList": [{"offerId": "9", "subject": "x", "price": 1}]}}}
    assert find_items(payload)[0]["offerId"] == "9"


def test_percentage_fields_are_normalised_to_unit_range():
    offer = build_offer({"offerId": "1", "subject": "T", "price": 1,
                         "sellerInfo": {"repurchaseRate": "38%", "responseRate": 0.9}}, "", "x")
    assert offer.supplier.repurchase_rate == 0.38
    assert offer.supplier.response_rate == 0.9


def test_price_ladder_selects_the_tier_the_quantity_unlocks():
    offer = build_offer({"offerId": "1", "subject": "T", "price": 20,
                         "priceRangeList": [{"startQuantity": 2, "price": 20},
                                            {"startQuantity": 500, "price": 15}]}, "", "x")
    assert offer.price_at_quantity(100) == 20
    assert offer.price_at_quantity(600) == 15


def test_dedupe_keeps_one_row_per_offer_and_records_every_query():
    a = build_offer({"offerId": "1", "subject": "T", "price": 1}, "卫衣", "x")
    b = build_offer({"offerId": "1", "subject": "T", "price": 1}, "帽衫", "x")
    merged = dedupe_offers([[a], [b]])
    assert len(merged) == 1
    assert "卫衣" in merged[0].source_query and "帽衫" in merged[0].source_query


# -------------------------------------------------------------------- mock


def test_mock_provider_searches_in_chinese_from_english(provider):
    results = provider.search("cotton t-shirt")
    assert results
    assert any("T恤" in o.title for o in results)


def test_mock_provider_round_trips_an_offer(provider):
    first = provider.search("纯棉T恤")[0]
    assert provider.get_offer(first.offer_id).offer_id == first.offer_id
    assert provider.get_offer("nope") is None
