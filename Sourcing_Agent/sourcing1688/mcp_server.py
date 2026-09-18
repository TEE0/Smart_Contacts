"""MCP server - the connector you attach to Claude or any other MCP client.

Design rule: **the tools carry the judgement, the model carries the language.**

Each tool returns structured data plus the reasoning behind it (which factor
scored what, which constraint eliminated which listing, what each cost component
was). The connecting model then does what it is actually good at - interpreting a
vague brief, reading results back in plain English, deciding what to ask next -
without ever being trusted to remember a MOQ ceiling across twenty listings.

Transports:
  * ``stdio``           - Claude Code, Claude Desktop, any local MCP client.
  * ``streamable-http`` - remote custom connectors (claude.ai), which cannot
                          launch a local process and require a public HTTPS URL.

The module works on both the 1.x (``FastMCP``) and 2.x (``MCPServer``) Python
SDKs, which are otherwise source-incompatible.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from . import __version__, costing, messaging, pipeline, preferences, scoring
from .config import get_settings, home_dir
from .lexicon import alternates, describe_terms, load_lexicon, translate_phrase, unknown_terms
from .models import Offer
from .planner import explain_plan, plan_queries
from .providers import ProviderError, available_providers, get_provider
from .store import delete_shortlist, load_shortlists, save_shortlist
from .supplier import infer_business_type, vet

SERVER_NAME = "1688-sourcing-agent"

INSTRUCTIONS = """\
Personalised apparel sourcing on 1688.com (Chinese wholesale).

How to use this connector well:

1. Call `get_preferences` first. Everything else is evaluated against that
   profile - price band, MOQ ceiling, banned fabrics, supplier floor, shipping
   destination. If the user states a preference in conversation, persist it with
   `update_preferences` rather than passing it ad hoc, so it applies next time.
2. For a full job, call `source_clothing` with the user's brief in plain
   English. It plans Chinese queries, searches, filters, vets suppliers, costs
   the order delivered, and returns a ranked shortlist with reasons.
3. Before spending API calls on a vague brief, `plan_search` shows exactly which
   Chinese queries would run and which English words could not be translated.
   Show the user the unrecognised words - a dropped adjective is the usual cause
   of a wrong shortlist.
4. `draft_supplier_message` writes the Chinese message to send the supplier,
   with an English back-translation. Show the user both before they send it.

Read the `rejected_sample` in results: an empty shortlist almost always means a
constraint is binding, not that the market is empty. Say which one.

Prices are in CNY unless a field says otherwise. Landed costs are estimates
built from stated assumptions, never quotes.
"""


# --------------------------------------------------------------------- SDK shim

def _build_server(name: str, instructions: str):
    """Construct a server on either the 1.x or 2.x Python MCP SDK.

    SDK 2.0 renamed ``FastMCP`` to ``MCPServer``. The decorator surface is
    otherwise the same, so one shim covers both rather than pinning users to a
    single major version of a dependency they may already have.
    """
    try:
        from mcp.server.mcpserver import MCPServer  # mcp >= 2

        return MCPServer(name=name, instructions=instructions, version=__version__)
    except ImportError:
        pass
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x

        # FastMCP 1.x takes no version argument.
        return FastMCP(name=name, instructions=instructions)
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise SystemExit(
            "The MCP SDK is not installed. Run:  pip install 'mcp>=1.13'\n"
            "(Both 1.x and 2.x are supported.)"
        ) from exc


mcp = _build_server(SERVER_NAME, INSTRUCTIONS)


# ----------------------------------------------------------------- helpers

def _profile() -> dict[str, Any]:
    return preferences.load_profile()


def _guard_write() -> dict[str, Any] | None:
    if get_settings().read_only:
        return {
            "error": "read_only",
            "message": "This connector is running with SOURCING_READ_ONLY=1, so preferences cannot be changed here.",
        }
    return None


def _fetch_offer(offer_id: str, provider_name: str | None = None) -> Offer | None:
    return get_provider(provider_name).get_offer(offer_id)


def _error(exc: Exception) -> dict[str, Any]:
    """Surface provider problems as data, not exceptions.

    A tool that raises gives the model nothing to act on; a tool that explains
    that the API key is missing lets it tell the user what to fix.
    """
    return {"error": type(exc).__name__, "message": str(exc)}


def _not_found(offer_id: str) -> dict[str, Any]:
    return {
        "error": "offer_not_found",
        "message": f"No listing {offer_id} from the active provider ({get_settings().provider}).",
    }


# ------------------------------------------------------------------- tools


@mcp.tool()
def get_preferences() -> dict[str, Any]:
    """Read the buyer's sourcing profile: style, sizing, price band, MOQ ceiling,
    materials, supplier standards, logistics and scoring weights.

    Call this before any sourcing work - every filter and score derives from it.
    """
    profile = _profile()
    return {
        "profile": profile,
        "summary": preferences.summarize(profile),
        "storage_path": str(home_dir()),
    }


@mcp.tool()
def update_preferences(patch: dict[str, Any], replace_lists: bool = False) -> dict[str, Any]:
    """Update part of the buyer's profile and persist it.

    Pass only the sections you are changing, e.g.
    {"commercial": {"max_unit_price_cny": 45, "max_moq": 50}}.

    replace_lists=False (default) merges list values - use it for "also avoid
    acrylic". replace_lists=True overwrites them - use it for "my colours are
    black and white, nothing else". Choosing wrongly silently keeps preferences
    the user meant to drop, so pick deliberately.

    Sections: identity, style, fit, materials, commercial, supplier, logistics,
    weights, notes.
    """
    blocked = _guard_write()
    if blocked:
        return blocked
    if not isinstance(patch, dict) or not patch:
        return {"error": "empty_patch", "message": "Pass the sections you want changed, e.g. {'commercial': {'max_moq': 50}}."}

    before = _profile()
    profile = preferences.update_profile(patch, replace_lists=replace_lists)
    changed = {
        section: {k: v for k, v in values.items() if before.get(section, {}).get(k) != v}
        for section, values in profile.items()
        if isinstance(values, dict) and before.get(section) != values
    }
    return {
        "profile": profile,
        "changed": {k: v for k, v in changed.items() if v},
        "summary": preferences.summarize(profile),
        "note": "Weights are re-normalised to sum to 1 so scores stay comparable.",
    }


@mcp.tool()
def reset_preferences() -> dict[str, Any]:
    """Restore the default sourcing profile, discarding every stored preference.

    This throws away all personalisation the buyer has accumulated - style,
    price band, supplier standards, weights. Confirm with them before calling it.
    """
    blocked = _guard_write()
    if blocked:
        return blocked
    profile = preferences.reset_profile()
    return {"profile": profile, "summary": preferences.summarize(profile)}


@mcp.tool()
def plan_search(brief: str, max_queries: int = 6) -> dict[str, Any]:
    """Show the Chinese 1688 queries a brief would produce, without searching.

    Use this on a vague or unusual brief. The response lists which English terms
    were recognised, which were dropped as untranslatable, and why each query
    exists. Show the user any unrecognised terms before running a real search.
    """
    profile = _profile()
    plans = plan_queries(brief, profile, max_queries=max_queries)
    return explain_plan(brief, profile, plans)


@mcp.tool()
def search_1688(
    query: str,
    limit: int = 20,
    page: int = 1,
    translate: bool = True,
    apply_filters: bool = True,
) -> dict[str, Any]:
    """Raw keyword search against 1688, scored against the buyer's profile.

    `query` may be English or Chinese; English is translated with the apparel
    lexicon unless translate=False. Use `source_clothing` for a full sourcing
    run - this tool is for spot checks and follow-up searches.
    """
    profile = _profile()
    zh = translate_phrase(query) if translate else query
    zh = zh or query
    try:
        offers = get_provider().search(zh, page=page, limit=limit)
    except ProviderError as exc:
        return _error(exc)

    passed, rejected = scoring.rank(
        offers, profile, brief=query, apply_hard_filter=apply_filters, enforce_garment_class=apply_filters
    )
    return {
        "query_sent": zh,
        "query_original": query,
        "provider": get_settings().provider,
        "results": [s.as_dict() for s in passed[:limit]],
        "filtered_out": [
            {"offer_id": s.offer.offer_id, "title": s.offer.title, "reasons": s.rejected_reasons}
            for s in rejected[:10]
        ],
        "note": "Ranked by your profile weights, not by 1688's own relevance order.",
    }


@mcp.tool()
def source_clothing(
    brief: str,
    top_n: int = 5,
    quantity: int | None = None,
    max_queries: int = 6,
    enrich: bool = True,
) -> dict[str, Any]:
    """Run the complete sourcing job for a clothing brief.

    Plans Chinese queries from the brief plus the stored profile, searches 1688
    across them, drops anything breaching a hard constraint, vets the surviving
    suppliers, estimates delivered cost per unit, and returns a ranked shortlist
    with the reasoning for every placement.

    `quantity` overrides the profile's planned order quantity - price ladders,
    freight and therefore the ranking all depend on it.

    Always read `rejected_sample` back to the user when the shortlist is short:
    it names the binding constraint.
    """
    # Qualified through the module: this function is named `source_clothing`
    # too, and the decorator rebinds that name at module level.
    try:
        return pipeline.source_clothing(
            brief, top_n=top_n, quantity=quantity, max_queries=max_queries, enrich=enrich
        )
    except ProviderError as exc:
        return _error(exc)


@mcp.tool()
def get_offer_details(offer_id: str) -> dict[str, Any]:
    """Full detail for one 1688 listing, scored and vetted against the profile."""
    try:
        offer = _fetch_offer(offer_id)
    except ProviderError as exc:
        return _error(exc)
    if not offer:
        return _not_found(offer_id)

    profile = _profile()
    scored = scoring.score_offer(offer, profile, apply_hard_filter=True)
    return {
        "offer": offer.as_dict(),
        "score": scored.score,
        "factors": [f.as_dict() for f in scored.factors],
        "risk_flags": scored.risk_flags,
        "strengths": scored.strengths,
        "blocked_by": scored.rejected_reasons,
        "business_type": infer_business_type(offer),
        "estimated_natural_fiber_pct": scoring.natural_fiber_pct(offer),
        "estimated_gsm": scoring.gsm(offer),
    }


@mcp.tool()
def vet_supplier(offer_id: str) -> dict[str, Any]:
    """Assess the seller behind a listing: trust score, risk flags, strengths.

    Reports factory-vs-trader, platform tenure, repurchase rate, TrustPass
    status and sample/OEM support, each with the reason it matters.
    """
    try:
        offer = _fetch_offer(offer_id)
    except ProviderError as exc:
        return _error(exc)
    if not offer:
        return _not_found(offer_id)

    profile = _profile()
    risks, strengths = vet(offer, profile)
    from .supplier import trust_score

    score, detail = trust_score(offer, profile)
    return {
        "offer_id": offer_id,
        "supplier": offer.supplier.as_dict(),
        "business_type": infer_business_type(offer),
        "trust_score": round(score, 4),
        "trust_detail": detail,
        "risk_flags": risks,
        "strengths": strengths,
        "meets_your_floor": not any("floor" in r or "ceiling" in r for r in risks),
    }


@mcp.tool()
def estimate_landed_cost(
    offer_id: str,
    quantity: int | None = None,
    shipping_mode: str | None = None,
    unit_weight_kg: float | None = None,
    duty_pct: float | None = None,
    import_tax_pct: float | None = None,
) -> dict[str, Any]:
    """Delivered cost per unit, itemised.

    Headline 1688 prices are not comparable across listings: freight is charged
    on weight, duty on value, and the agent fee on goods only. shipping_mode is
    express | air | sea. Every rate used is returned under `assumptions` - all
    of them are overridable and none of them is a quote.
    """
    try:
        offer = _fetch_offer(offer_id)
    except ProviderError as exc:
        return _error(exc)
    if not offer:
        return _not_found(offer_id)

    overrides: dict[str, Any] = {}
    if unit_weight_kg is not None:
        overrides["unit_weight_kg"] = unit_weight_kg
    if duty_pct is not None:
        overrides["duty_pct"] = duty_pct
    if import_tax_pct is not None:
        overrides["import_tax_pct"] = import_tax_pct

    return costing.landed_cost(
        offer, _profile(), quantity=quantity, shipping_mode=shipping_mode, overrides=overrides
    )


@mcp.tool()
def compare_listings(offer_ids: list[str], quantity: int | None = None) -> dict[str, Any]:
    """Head-to-head comparison of specific listings the user already has in hand.

    Hard filters are reported but not applied - if the user asked about these
    listings, they should see them even when one breaches a constraint.
    """
    try:
        return pipeline.compare_offers(offer_ids, quantity=quantity)
    except ProviderError as exc:
        return _error(exc)


@mcp.tool()
def search_by_image(image_url: str, limit: int = 15) -> dict[str, Any]:
    """Reverse image search - find 1688 listings resembling a reference photo.

    This is how buyers actually source a garment they have seen elsewhere.
    Requires a provider that supports image search; the mock provider does not
    and says so in the response.
    """
    try:
        offers = get_provider().search_by_image(image_url, limit=limit)
    except ProviderError as exc:
        return _error(exc)

    profile = _profile()
    passed, rejected = scoring.rank(offers, profile, enforce_garment_class=False)
    return {
        "image_url": image_url,
        "provider": get_settings().provider,
        "results": [s.as_dict() for s in passed[:limit]],
        "filtered_out": [{"offer_id": s.offer.offer_id, "reasons": s.rejected_reasons} for s in rejected[:8]],
    }


@mcp.tool()
def draft_supplier_message(
    offer_id: str,
    kind: str = "inquiry",
    quantity: int | None = None,
    target_price_cny: float | None = None,
    extra_questions: list[str] | None = None,
) -> dict[str, Any]:
    """Draft the Chinese message to send a 1688 supplier, with a back-translation.

    kind: inquiry | sample | negotiate | customization | qc | followup.

    1688 sellers reply in Chinese on Aliwangwang and largely ignore English.
    Show the user both the Chinese text and the English back-translation before
    they send it - it is written in their name.
    """
    try:
        offer = _fetch_offer(offer_id)
    except ProviderError as exc:
        return _error(exc)
    if not offer:
        return _not_found(offer_id)
    try:
        return messaging.draft_message(
            offer, _profile(), kind,
            quantity=quantity, target_price_cny=target_price_cny, extra_questions=extra_questions,
        )
    except ValueError as exc:
        return _error(exc)


@mcp.tool()
def rfq_checklist() -> dict[str, Any]:
    """The checklist of things to settle with a supplier before money moves.

    Grouped into what to confirm before sampling, before placing a bulk order,
    and what documentation the destination country's import process needs.
    """
    return messaging.rfq_checklist(_profile())


@mcp.tool()
def translate_sourcing_terms(text: str) -> dict[str, Any]:
    """Translate English apparel/sourcing terms into the Chinese 1688 sellers use.

    Also reports which words the lexicon could not map, so the user can supply
    the Chinese term for anything niche.
    """
    return {
        "input": text,
        "query_zh": translate_phrase(text),
        "alternate_phrasings": alternates(text),
        "recognized": describe_terms(text),
        "unrecognized": unknown_terms(text),
    }


@mcp.tool(name="save_shortlist")
def save_shortlist_tool(name: str, offer_ids: list[str], brief: str = "") -> dict[str, Any]:
    """Save listings under a name so they can be revisited in a later session."""
    blocked = _guard_write()
    if blocked:
        return blocked

    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for offer_id in offer_ids:
        try:
            offer = _fetch_offer(offer_id)
        except ProviderError as exc:
            missing.append(f"{offer_id}: {exc}")
            continue
        if offer:
            entries.append(
                {
                    "offer_id": offer.offer_id,
                    "title": offer.title,
                    "url": offer.url,
                    "price_cny": offer.price_cny,
                    "moq": offer.moq,
                    "supplier": offer.supplier.name,
                }
            )
        else:
            missing.append(f"{offer_id}: not found")

    record = save_shortlist(name, entries, brief=brief)
    return {"saved": record, "missing": missing}


@mcp.tool()
def list_shortlists() -> dict[str, Any]:
    """List the shortlists saved in earlier sessions, with their briefs and dates.

    Use this when the user refers back to work from a previous conversation
    ("the hoodie suppliers from last week") - the profile persists, but nothing
    else about a past session does.
    """
    return {"shortlists": load_shortlists()}


@mcp.tool(name="delete_shortlist")
def delete_shortlist_tool(name: str) -> dict[str, Any]:
    """Delete a saved shortlist by name.

    Names come from `list_shortlists`. Deleting is permanent and affects only
    the saved list, never the buyer's preference profile.
    """
    blocked = _guard_write()
    if blocked:
        return blocked
    return {"deleted": delete_shortlist(name), "name": name}


@mcp.tool()
def provider_status() -> dict[str, Any]:
    """Which 1688 data source is active, and whether it is configured.

    Call this when results look empty or synthetic - the default provider is a
    fixture catalogue, not the live marketplace.
    """
    settings = get_settings()
    try:
        health = get_provider().health()
    except ProviderError as exc:
        health = _error(exc)
    return {
        "active_provider": settings.provider,
        "available_providers": available_providers(),
        "health": health,
        "read_only": settings.read_only,
        "how_to_switch": (
            "Set SOURCING_PROVIDER=official with SOURCING_1688_APP_KEY/_APP_SECRET/_ACCESS_TOKEN, "
            "or SOURCING_PROVIDER=aggregator with SOURCING_AGG_BASE_URL/_API_KEY."
        ),
    }


# --------------------------------------------------------------- resources


@mcp.resource("sourcing://profile")
def profile_resource() -> str:
    """The buyer's current sourcing profile, as a readable summary."""
    return preferences.summarize(_profile())


@mcp.resource("sourcing://lexicon")
def lexicon_resource() -> str:
    """The English -> Chinese apparel sourcing lexicon, grouped by term type."""
    lines = []
    for group, mapping in load_lexicon().items():
        lines.append(f"## {group} ({len(mapping)} terms)")
        lines.extend(f"  {en} -> {', '.join(zh)}" for en, zh in mapping.items())
    return "\n".join(lines)


@mcp.resource("sourcing://shortlists")
def shortlists_resource() -> str:
    """Saved shortlists from previous sessions."""
    data = load_shortlists()
    if not data:
        return "No saved shortlists."
    lines = []
    for name, record in data.items():
        lines.append(f"## {name} ({record.get('count', 0)} items, saved {record.get('saved_at')})")
        if record.get("brief"):
            lines.append(f"   brief: {record['brief']}")
        lines.extend(f"   - {e['offer_id']} {e.get('title', '')[:60]} ¥{e.get('price_cny', 0)}" for e in record.get("entries", []))
    return "\n".join(lines)


# ----------------------------------------------------------------- prompts


@mcp.prompt()
def sourcing_brief(garment: str, quantity: str = "", budget: str = "") -> str:
    """Start a structured sourcing run for a garment."""
    parts = [f"Source {garment} from 1688 for me."]
    if quantity:
        parts.append(f"I plan to order about {quantity} units.")
    if budget:
        parts.append(f"My budget is {budget}.")
    parts.append(
        "Check my stored preferences first, show me the Chinese queries you will run "
        "(and anything you could not translate), then give me a shortlist with landed "
        "cost per unit and the reason each supplier made or missed the list."
    )
    return " ".join(parts)


# -------------------------------------------------------------------- entry


def _http_app(path: str, host: str):
    """Build the streamable-HTTP ASGI app, with optional bearer-token auth."""
    try:
        app = mcp.streamable_http_app(streamable_http_path=path, host=host)
    except TypeError:  # mcp 1.x takes no arguments here
        app = mcp.streamable_http_app()

    token = get_settings().http_bearer_token
    if not token:
        return app

    # A remote connector is reachable by anyone who learns the URL. This is a
    # deliberately blunt shared-secret gate, not an OAuth implementation - if
    # you need per-user identity, put a real authorising proxy in front.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            header = request.headers.get("authorization", "")
            if header.removeprefix("Bearer ").strip() != token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app.add_middleware(BearerAuth)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1688 sourcing agent - MCP server")
    parser.add_argument(
        "--transport", default=os.environ.get("SOURCING_TRANSPORT", "stdio"),
        choices=["stdio", "http", "streamable-http", "sse"],
        help="stdio for local clients (Claude Code/Desktop); http for a remote custom connector.",
    )
    parser.add_argument("--host", default=os.environ.get("SOURCING_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or os.environ.get("SOURCING_PORT") or 8765))
    parser.add_argument("--path", default=os.environ.get("SOURCING_HTTP_PATH", "/mcp"))
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        mcp.run()
        return 0

    if args.transport == "sse":
        # Superseded by streamable HTTP in the March 2025 spec revision and being
        # retired by clients; supported only for older deployments.
        print("warning: SSE transport is deprecated - prefer --transport http", file=sys.stderr)
        mcp.run(transport="sse")
        return 0

    try:
        import uvicorn
    except ImportError:
        print(
            "Remote transport needs an ASGI server:  pip install 'sourcing1688[http]'  (or: pip install uvicorn)",
            file=sys.stderr,
        )
        return 1

    settings = get_settings()
    if not settings.http_bearer_token:
        print(
            "warning: SOURCING_HTTP_BEARER_TOKEN is unset - this endpoint will accept any caller. "
            "Set it before exposing the server publicly.",
            file=sys.stderr,
        )
    print(f"1688 sourcing agent on http://{args.host}:{args.port}{args.path}  (provider: {settings.provider})", file=sys.stderr)
    uvicorn.run(_http_app(args.path, args.host), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
