"""Command-line access to the same engine the MCP tools expose.

Useful for three things the connector cannot do for you: checking your
credentials work before wiring the connector up, tuning a profile without
burning conversation turns, and running a sourcing sweep from cron.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import costing, messaging, preferences
from .config import get_settings, home_dir
from .lexicon import alternates, describe_terms, translate_phrase, unknown_terms
from .pipeline import source_clothing
from .planner import explain_plan, plan_queries
from .providers import ProviderError, get_provider


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        health = get_provider().health()
    except ProviderError as exc:
        health = {"error": str(exc)}
    _dump({"provider": settings.provider, "profile_path": str(home_dir()), "health": health})
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    profile = preferences.load_profile()
    if args.json:
        _dump(profile)
    else:
        print(preferences.summarize(profile))
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    try:
        patch = json.loads(args.patch)
    except json.JSONDecodeError as exc:
        print(f"--patch must be JSON: {exc}", file=sys.stderr)
        return 2
    profile = preferences.update_profile(patch, replace_lists=args.replace_lists)
    print(preferences.summarize(profile))
    return 0


def _cmd_translate(args: argparse.Namespace) -> int:
    _dump(
        {
            "input": args.text,
            "query_zh": translate_phrase(args.text),
            "alternates": alternates(args.text),
            "recognized": describe_terms(args.text),
            "unrecognized": unknown_terms(args.text),
        }
    )
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    profile = preferences.load_profile()
    _dump(explain_plan(args.brief, profile, plan_queries(args.brief, profile, max_queries=args.max_queries)))
    return 0


def _cmd_source(args: argparse.Namespace) -> int:
    result = source_clothing(
        args.brief, top_n=args.top, quantity=args.quantity, max_queries=args.max_queries, enrich=not args.no_enrich
    )
    if args.json:
        _dump(result)
        return 0

    print(f"Brief: {result['brief']}")
    print(f"Provider: {result['provider']} | found {result.get('candidates_found', 0)} "
          f"| passed {result.get('candidates_passed', 0)} | rejected {result.get('candidates_rejected', 0)}")
    print("\nQueries run:")
    for plan in result["plan"]["queries"]:
        print(f"  [{plan['intent']:10}] {plan['query_zh']}")
    if result["plan"].get("translation_warning"):
        print(f"\n! {result['plan']['translation_warning']}")

    print("\nShortlist:")
    for index, entry in enumerate(result["shortlist"], 1):
        offer, landed = entry["offer"], entry.get("landed_cost") or {}
        print(f"\n{index}. [{entry['score']:.3f}] {offer['title'][:70]}")
        print(f"   {offer['url']}")
        print(f"   ¥{offer['price_cny']:.2f}/unit listed, MOQ {offer['moq']} | "
              f"landed {landed.get('landed_unit_cost', '?')} {landed.get('currency', '')}/unit at {landed.get('quantity', '?')} pcs")
        print(f"   Supplier: {offer['supplier'].get('name', '?')} ({offer['supplier'].get('province', '')})")
        for strength in entry["strengths"][:3]:
            print(f"   + {strength}")
        for risk in entry["risk_flags"][:3]:
            print(f"   ! {risk}")

    if result.get("rejected_sample"):
        print("\nRejected (sample):")
        for entry in result["rejected_sample"]:
            print(f"  - {entry['offer_id']} {entry['title'][:44]}: {entry['reasons'][0]}")
    print("\nNext steps:")
    for step in result.get("next_steps", []):
        print(f"  * {step}")
    return 0


def _cmd_cost(args: argparse.Namespace) -> int:
    offer = get_provider().get_offer(args.offer_id)
    if not offer:
        print(f"Offer {args.offer_id} not found", file=sys.stderr)
        return 1
    _dump(costing.landed_cost(offer, preferences.load_profile(), quantity=args.quantity, shipping_mode=args.mode))
    return 0


def _cmd_message(args: argparse.Namespace) -> int:
    offer = get_provider().get_offer(args.offer_id)
    if not offer:
        print(f"Offer {args.offer_id} not found", file=sys.stderr)
        return 1
    draft = messaging.draft_message(offer, preferences.load_profile(), args.kind, quantity=args.quantity)
    print(draft["message_zh"])
    print("\n--- English back-translation ---")
    print(draft["message_en_backtranslation"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sourcing1688-cli", description="1688 apparel sourcing agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show the active provider and configuration").set_defaults(func=_cmd_status)

    p_profile = sub.add_parser("profile", help="Show the buyer profile")
    p_profile.add_argument("--json", action="store_true")
    p_profile.set_defaults(func=_cmd_profile)

    p_set = sub.add_parser("set", help="Patch the profile with a JSON fragment")
    p_set.add_argument("patch", help='e.g. \'{"commercial": {"max_moq": 50}}\'')
    p_set.add_argument("--replace-lists", action="store_true", help="Replace list values instead of merging them")
    p_set.set_defaults(func=_cmd_set)

    p_tr = sub.add_parser("translate", help="Translate an English sourcing phrase to Chinese")
    p_tr.add_argument("text")
    p_tr.set_defaults(func=_cmd_translate)

    p_plan = sub.add_parser("plan", help="Show the queries a brief would run, without searching")
    p_plan.add_argument("brief")
    p_plan.add_argument("--max-queries", type=int, default=6)
    p_plan.set_defaults(func=_cmd_plan)

    p_src = sub.add_parser("source", help="Run a full sourcing job")
    p_src.add_argument("brief")
    p_src.add_argument("--top", type=int, default=5)
    p_src.add_argument("--quantity", type=int, default=None)
    p_src.add_argument("--max-queries", type=int, default=6)
    p_src.add_argument("--no-enrich", action="store_true", help="Skip per-offer detail calls")
    p_src.add_argument("--json", action="store_true")
    p_src.set_defaults(func=_cmd_source)

    p_cost = sub.add_parser("cost", help="Landed cost for one listing")
    p_cost.add_argument("offer_id")
    p_cost.add_argument("--quantity", type=int, default=None)
    p_cost.add_argument("--mode", choices=["express", "air", "sea"], default=None)
    p_cost.set_defaults(func=_cmd_cost)

    p_msg = sub.add_parser("message", help="Draft a Chinese supplier message")
    p_msg.add_argument("offer_id")
    p_msg.add_argument("--kind", choices=list(messaging.MESSAGE_KINDS), default="inquiry")
    p_msg.add_argument("--quantity", type=int, default=None)
    p_msg.set_defaults(func=_cmd_message)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ProviderError as exc:
        print(f"Provider error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
