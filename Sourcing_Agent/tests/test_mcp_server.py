"""Tests for the connector surface itself.

The tools are what an AI client sees, so the contract under test is: they all
register, they return JSON-serialisable data, and they degrade into an
explanatory payload rather than raising when something upstream is wrong.
"""

from __future__ import annotations

import json

import pytest

from sourcing1688.mcp_server import mcp

EXPECTED_TOOLS = {
    "get_preferences", "update_preferences", "reset_preferences", "plan_search",
    "search_1688", "source_clothing", "get_offer_details", "vet_supplier",
    "estimate_landed_cost", "compare_listings", "search_by_image",
    "draft_supplier_message", "rfq_checklist", "translate_sourcing_terms",
    "save_shortlist", "list_shortlists", "delete_shortlist", "provider_status",
}


def unwrap(result):
    """Normalise a call_tool result across mcp 1.x and 2.x."""
    for attr in ("structuredContent", "structured_content"):
        value = getattr(result, attr, None)
        if value is not None:
            return value.get("result", value) if isinstance(value, dict) and set(value) == {"result"} else value
    content = getattr(result, "content", result)
    if isinstance(content, tuple):
        content = content[0]
    if isinstance(content, list) and content and hasattr(content[0], "text"):
        try:
            return json.loads(content[0].text)
        except json.JSONDecodeError:
            return content[0].text
    return content


async def call(tool_name, /, **arguments):
    # Positional-only so a tool argument literally called "name" does not
    # collide with this helper's own parameter.
    return unwrap(await mcp.call_tool(tool_name, arguments))


@pytest.mark.asyncio
async def test_every_tool_is_registered():
    names = {tool.name for tool in await mcp.list_tools()}
    assert EXPECTED_TOOLS <= names


@pytest.mark.asyncio
async def test_every_tool_documents_itself():
    # The description is the only thing an AI client has to choose a tool with.
    for tool in await mcp.list_tools():
        assert tool.description and len(tool.description) > 40, f"{tool.name} is under-documented"


@pytest.mark.asyncio
async def test_resources_and_prompt_are_registered():
    assert {str(r.uri) for r in await mcp.list_resources()} >= {
        "sourcing://profile", "sourcing://lexicon", "sourcing://shortlists"
    }
    assert {p.name for p in await mcp.list_prompts()} == {"sourcing_brief"}


@pytest.mark.asyncio
async def test_preferences_round_trip_through_the_tools():
    updated = await call("update_preferences", patch={"commercial": {"max_moq": 42}})
    assert updated["profile"]["commercial"]["max_moq"] == 42
    assert updated["changed"]["commercial"]["max_moq"] == 42
    assert (await call("get_preferences"))["profile"]["commercial"]["max_moq"] == 42


@pytest.mark.asyncio
async def test_empty_patch_is_rejected_with_guidance():
    result = await call("update_preferences", patch={})
    assert result["error"] == "empty_patch"
    assert "commercial" in result["message"]


@pytest.mark.asyncio
async def test_read_only_mode_blocks_writes(monkeypatch):
    from sourcing1688 import config

    monkeypatch.setenv("SOURCING_READ_ONLY", "1")
    config.get_settings(refresh=True)
    try:
        result = await call("update_preferences", patch={"notes": "nope"})
        assert result["error"] == "read_only"
    finally:
        monkeypatch.delenv("SOURCING_READ_ONLY")
        config.get_settings(refresh=True)


@pytest.mark.asyncio
async def test_missing_offer_returns_an_explanation_not_an_exception():
    for tool in ("get_offer_details", "vet_supplier", "estimate_landed_cost"):
        result = await call(tool, offer_id="000000")
        assert result["error"] == "offer_not_found"


@pytest.mark.asyncio
async def test_invalid_message_kind_is_reported_as_data():
    result = await call("draft_supplier_message", offer_id="610001", kind="wheedle")
    assert "kind must be one of" in result["message"]


@pytest.mark.asyncio
async def test_source_clothing_tool_is_not_self_recursive():
    # Regression: the tool decorator rebinds the module-level name, so a tool
    # named after the function it delegates to would call itself forever.
    result = await call("source_clothing", brief="cotton t-shirt", top_n=2)
    assert result["status"] in {"ok", "no_results"}


@pytest.mark.asyncio
async def test_plan_search_does_not_hit_the_provider():
    result = await call("plan_search", brief="heavyweight cotton tee")
    assert result["queries"]
    assert "shortlist" not in result


@pytest.mark.asyncio
async def test_provider_status_explains_the_mock_default():
    status = await call("provider_status")
    assert status["active_provider"] == "mock"
    assert "SOURCING_PROVIDER" in status["how_to_switch"]


@pytest.mark.asyncio
async def test_shortlists_round_trip():
    saved = await call("save_shortlist", name="spring-tees", offer_ids=["610001", "610002"], brief="tees")
    assert saved["saved"]["count"] == 2
    assert "spring-tees" in (await call("list_shortlists"))["shortlists"]
    assert (await call("delete_shortlist", name="spring-tees"))["deleted"] is True


@pytest.mark.asyncio
async def test_all_tool_results_are_json_serialisable():
    for name, arguments in [
        ("get_preferences", {}),
        ("rfq_checklist", {}),
        ("translate_sourcing_terms", {"text": "heavyweight cotton tee"}),
        ("search_1688", {"query": "cotton t-shirt", "limit": 3}),
        ("source_clothing", {"brief": "cotton t-shirt", "top_n": 2}),
        ("get_offer_details", {"offer_id": "610001"}),
        ("estimate_landed_cost", {"offer_id": "610001", "quantity": 100}),
        ("draft_supplier_message", {"offer_id": "610001", "kind": "sample"}),
        ("compare_listings", {"offer_ids": ["610001", "610002"]}),
    ]:
        json.dumps(await call(name, **arguments), ensure_ascii=False)
