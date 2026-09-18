# 1688 Sourcing Agent

A personalised clothing-sourcing agent for [1688.com](https://1688.com), China's
wholesale marketplace, exposed as an **MCP connector** you can attach to Claude
or any other MCP-capable AI.

You tell it what you like once. After that, "find me heavyweight oversized tees
with an embroidered logo" runs the whole job: it translates the brief into the
Chinese that 1688 sellers actually write, searches across several phrasings,
eliminates anything breaching your constraints, vets the suppliers behind the
survivors, works out what each option costs *delivered to you*, and hands back a
ranked shortlist where every placement is explained — plus the Chinese message to
send the supplier.

---

## The design decision that matters

**The tools carry the judgement. The connecting model carries the language.**

Scoring, filtering, supplier vetting and cost arithmetic are deterministic
Python. The AI interprets your vague brief, reads results back in English, and
decides what to ask next.

This split is deliberate. An LLM asked to eyeball twenty listings against nine
constraints will quietly forget the MOQ ceiling somewhere around the nineteenth
— and it will not tell you it did. A filter does not forget, and it can name
exactly which rule eliminated which listing. Equally, no rule engine will ever
parse *"something like what I bought last spring but warmer"*. Each side does
what it is good at.

The practical consequence: **every ranking is auditable**. Each result carries
its per-factor scores, weights, risk flags and the reason each rejected listing
was rejected.

---

## What it actually does

### 1. It searches in Chinese, because 1688 is a Chinese marketplace

Searching 1688 in English returns a thin, unrepresentative slice of the
catalogue. The agent ships a curated apparel lexicon (265 terms across
garments, fabrics, fits, construction details, colours and trade jargon) and
translates your brief into the phrasing sellers use:

```
"heavyweight oversized cotton tee with embroidered logo"
  → 男女同款宽松重磅刺绣logo纯棉短袖T恤
```

Not a literal translation — trade phrasing. A "heavyweight oversized tee" is
listed as 重磅宽松短袖T恤, never as a word-for-word rendering.

It also **tells you what it could not translate**. If you ask for "bouclé trim"
and the lexicon has no entry, that word is reported as dropped rather than
silently discarded — a missing adjective is the usual reason a shortlist comes
back subtly wrong.

### 2. It fans out, because one query is not a sourcing strategy

1688's keyword index is literal and its supplier population is fragmented.
卫衣 and 帽衫 are the same garment but surface largely different factories, and
adding 定制 swaps a catalogue of stock resellers for one of OEM workshops. So
each run plans several queries with distinct purposes:

| Intent | Purpose |
|---|---|
| `primary` | your brief plus your standing preferences |
| `variant` | synonym phrasings of the same garment |
| `material` | the brief pinned to each required fabric |
| `commercial` | pinned to your buying model (OEM / stock / dropship) |
| `fallback` | deliberately broad, to establish the market price band |

### 3. It separates rules from preferences

- **Hard constraints** eliminate outright: MOQ ceiling, banned fabric, price
  ceiling, budget, blocked seller, missing certification, counterfeit language.
- **Weights** decide which survivor is best: price, material match, supplier
  trust, style, MOQ fit, traction, customisation support.

Conflating the two is the classic sourcing-tool mistake. "Never above 500 MOQ"
does not mean "prefer lower MOQ", and treating it as a preference lets a
beautiful 2000-MOQ listing win a shortlist you cannot buy from.

### 4. It ranks on landed cost, not sticker price

A ¥19 tee from a 2-piece-MOQ reseller and a ¥27 tee from a factory at 500 units
are not comparable numbers. Freight is charged on weight, duty on declared
value, the agent's fee on goods only. Listings that look 40% apart routinely
land within pennies of each other — and the order flips once your real quantity,
shipping mode and destination duty rate are applied.

Every cost component and every assumption is returned. They are estimates, never
quotes.

### 5. It vets the supplier, not just the product

Years on platform, 诚信通 (TrustPass), 复购率 (repurchase rate — the single most
predictive field on the platform), response rate, refund rate, and factory vs
trading company inferred from the company name. Plus price-outlier detection:
a listing at less than half its peers is not a bargain, it is a different
product.

### 6. It tells you why you got nothing

An empty shortlist is diagnosed, not shrugged at:

```
Nothing cleared your hard constraints.
  * 2 on-brief listing(s) blocked by your ¥8000 budget. The cheapest of them
    needs ¥8200 at 200 units - raise the budget to about ¥8610, or drop the
    planned quantity to roughly 195 units.
  * 1 on-brief listing(s) blocked by your ¥60.00 unit-price ceiling. The
    cheapest of them is ¥61.00 at 200 units - a ¥1.00 gap.
  * Your constraints are binding - the market has matching products you have
    ruled out.
```

It distinguishes "your search found the wrong products" from "your search found
the right products and your rules excluded them" — the remedies are opposite.

### 7. It writes the message to send

1688 sellers answer 阿里旺旺 messages in Chinese and largely ignore English. The
agent drafts inquiry, sample-request, negotiation, customisation and QC
messages in Chinese, **with an English back-translation** so you always know
what is being sent in your name. Templates, not model output — a negotiation
message should never invent a commitment you did not authorise.

---

## Getting access to 1688 data

**There is no single public 1688 API.** Be sceptical of any tool that implies
otherwise. There are three realistic routes, and which you can use depends on
paperwork, not code:

| Provider | What it is | Trade-off |
|---|---|---|
| `mock` *(default)* | Fixture catalogue of 22 listings and 17 sellers | No credentials, no cost. Runs the full workflow so you can evaluate it before paying for anything. |
| `official` | Alibaba/1688 open platform (app key + secret + OAuth token) | Real data. Namespaces are granted per application, and most overseas accounts get only a cross-border subset. |
| `aggregator` | A licensed third-party data API | Works the same afternoon. Per-call cost, varying field coverage. |

The agent programs against one interface, so **all the intelligence above works
identically on any of the three**. Switching is an environment variable.

The default is `mock` on purpose: a shopping agent that silently returns nothing
is worse than one that is obviously running on samples. `provider_status` always
tells you which is active.

---

## Install

```bash
cd Sourcing_Agent
pip install -e .              # or: pip install -r requirements.txt
```

Python 3.10+. Works on both major MCP SDK lines — 2.0 renamed `FastMCP` to
`MCPServer`, and the server shims over the difference.

Verify:

```bash
python -m sourcing1688.cli status
python -m sourcing1688.cli source "heavyweight cotton tee with embroidered logo"
```

---

## Connect it to your AI

### Claude Code

```bash
claude mcp add sourcing1688 -- python /absolute/path/to/Sourcing_Agent/run_server.py
```

### Claude Desktop

Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "sourcing1688": {
      "command": "python",
      "args": ["/absolute/path/to/Sourcing_Agent/run_server.py"],
      "env": {
        "SOURCING_PROVIDER": "mock",
        "SOURCING_CURRENCY": "GBP",
        "SOURCING_DUTY_PCT": "12"
      }
    }
  }
}
```

Restart Claude Desktop, then ask: *"What are my 1688 sourcing preferences?"*

### claude.ai as a remote custom connector

Remote connectors cannot launch a local process — they need a public HTTPS URL
speaking streamable HTTP:

```bash
pip install -e '.[http]'
export SOURCING_HTTP_BEARER_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
python run_server.py --transport http --host 0.0.0.0 --port 8765
```

Put it behind HTTPS (a reverse proxy or a tunnel), then add
`https://your-host/mcp` under **Settings → Connectors → Add custom connector**.

**Set `SOURCING_HTTP_BEARER_TOKEN` before exposing this publicly.** Without it
anyone who learns the URL can spend your API quota. The built-in check is a
blunt shared secret, not an OAuth implementation — if you need per-user
identity, put a real authorising proxy in front.

---

## Personalising it

The profile is the whole point. Set it once in conversation:

> "I'm a small streetwear brand shipping to the UK. Never more than 100 MOQ,
>  target ¥35 a piece, ceiling ¥60. Cotton only — at least 80% natural fibre,
>  and never PU leather. I need suppliers who do custom logos and samples, been
>  on 1688 at least 3 years. Ship by air."

The AI turns that into an `update_preferences` call. It persists to
`~/.config/sourcing1688/profile.json` and applies to every future session.

Nine sections: `identity`, `style`, `fit`, `materials`, `commercial`,
`supplier`, `logistics`, `weights`, `notes`.

One subtlety worth knowing: list updates **merge** by default ("also avoid
acrylic") and replace only when asked ("my colours are black and white, nothing
else"). Getting this backwards silently keeps preferences you meant to drop, so
the tool makes the caller choose.

Scoring weights are yours to move:

```
"Care much more about supplier reliability than price."
  → weights: supplier_trust up, price down (re-normalised to sum to 1)
```

---

## The tools

| Tool | Purpose |
|---|---|
| `get_preferences` / `update_preferences` / `reset_preferences` | Read and personalise the profile |
| `plan_search` | Show the Chinese queries a brief would run — without spending API calls |
| `source_clothing` | The whole job: plan → search → filter → vet → cost → rank |
| `search_1688` | Raw keyword search, scored against your profile |
| `get_offer_details` | One listing, fully scored and vetted |
| `vet_supplier` | Trust score, risk flags and strengths for a seller |
| `estimate_landed_cost` | Delivered cost per unit, itemised |
| `compare_listings` | Head-to-head on listings you already found |
| `search_by_image` | Reverse image search from a reference photo |
| `draft_supplier_message` | Chinese message + English back-translation |
| `rfq_checklist` | What to settle before sampling, before bulk, for import |
| `translate_sourcing_terms` | English → Chinese trade terms, with gaps reported |
| `save_shortlist` / `list_shortlists` / `delete_shortlist` | Persist work across sessions |
| `provider_status` | Which data source is live, and whether it is configured |

Resources: `sourcing://profile`, `sourcing://lexicon`, `sourcing://shortlists`.
Prompt: `sourcing_brief`.

---

## CLI

The same engine, without an AI — useful for checking credentials before wiring
up the connector, and for cron.

```bash
sourcing1688-cli status
sourcing1688-cli profile
sourcing1688-cli set '{"commercial": {"max_moq": 50, "target_unit_price_cny": 28}}'
sourcing1688-cli translate "washed denim jacket with sherpa lining"
sourcing1688-cli plan "heavyweight cotton hoodie"
sourcing1688-cli source "heavyweight cotton hoodie" --quantity 300
sourcing1688-cli cost 610001 --quantity 500 --mode sea
sourcing1688-cli message 610001 --kind negotiate
```

---

## Tests

```bash
pip install -e '.[dev]'
pytest
```

105 tests, no network required. Several encode regressions worth keeping:

- adjacent lexicon terms blocking each other (a char-offset matcher let each
  match swallow its neighbour's leading space)
- `95%棉 5%氨纶` parsed as 49% natural fibre (a fixed-width lookahead attributed
  the neighbouring fibre name to the wrong percentage)
- the garment noun being dropped by query truncation, leaving a query of pure
  adjectives that matches every category at once
- an embroidered cotton **cap** ranking highly in a search for embroidered
  cotton **tees** — relevance has to be a gate, not a weighted factor
- the `source_clothing` tool calling itself forever, because the MCP decorator
  rebinds the module-level name it delegates through

---

## Limits, honestly

- **The default provider is fixture data.** Nothing is bought, and no live
  prices are fetched, until you configure `official` or `aggregator`.
- **Landed costs are estimates.** Freight bills on the greater of actual and
  volumetric weight, and every rate here is a default you should replace with
  your forwarder's. Get carton dimensions from the supplier before committing.
- **Unit weights are inferred** from garment type when a listing does not
  publish one. Ask for the real figure — it moves the freight number materially.
- **No payment, no ordering.** The agent researches, ranks and drafts. Placing
  an order, paying a deposit and agreeing terms stay with you, deliberately.
- **Composition parsing is best-effort.** 1688 composition text is free-form;
  where a listing states nothing the agent reports "unknown" rather than scoring
  it as zero, and says so.
- **Respect the platform.** Use the official API or a licensed data vendor.
  Scraping 1688 violates its terms of service, and this package does not do it.

---

## Layout

```
Sourcing_Agent/
├── run_server.py              # entry point (stdio or remote HTTP)
├── sourcing1688/
│   ├── mcp_server.py          # the connector: 18 tools, 3 resources, 1 prompt
│   ├── cli.py                 # same engine, terminal interface
│   ├── preferences.py         # the profile: hard constraints vs weights
│   ├── lexicon.py             # EN → ZH trade-term translation
│   ├── planner.py             # brief + profile → Chinese query set
│   ├── scoring.py             # hard filters, weighted factors, diagnosis
│   ├── supplier.py            # vetting rules and trust scoring
│   ├── costing.py             # landed cost per unit
│   ├── messaging.py           # Chinese supplier messages + back-translation
│   ├── pipeline.py            # end-to-end orchestration
│   ├── providers/             # mock | official (AOP-signed) | aggregator
│   └── data/                  # lexicon + fixture catalogue
└── tests/                     # 105 tests
```

## Licence

Apache 2.0, matching the parent repository.
