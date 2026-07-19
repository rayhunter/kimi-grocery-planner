# 🛒 Kimi K3 Multi-Agent Grocery Shopping Planner

> Compare grocery prices across **all stores near you** — chains, local markets, big-box stores — including weekly deals, membership discounts, and percentage-off promotions. Powered by **Pydantic AI** + **Kimi K3** (2.8T parameters, 1M context window), with every price **verified against its web source** before it reaches your report.

## What It Does

Ask it something like: *"Are cherry tomatoes cheaper at Super Target or Whole Foods today?"* — and it will:

1. 🔍 **Discover** grocery stores near your location (chains, local markets, Costco, Target, Whole Foods, etc.)
2. 💰 **Scout prices** concurrently across all stores for your items
3. ✅ **Verify** every reported price against the raw search results — fabricated prices are discarded, unverified ones are flagged
4. 🎟️ **Identify deals** — weekly sales, membership prices (Target Circle, Amazon Prime, Kroger Plus), percentage-off coupons, digital deals
5. 🧠 **Reason with Kimi K3** to rank, compare, and recommend the best buys — fairly, within comparable units
6. 🗺️ **Optimize your trip** — suggest which 1–2 stores give you the best basket price

## Quickstart

```bash
# 1. Install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure — get a key at https://platform.moonshot.ai
cp .env.example .env   # then set MOONSHOT_API_KEY (required)

# 3. Run
python main.py "Austin, TX" "cherry tomatoes" "broccoli"
```

More invocation styles:

```bash
python main.py --locale "Seattle, WA" --items "salmon fillet" "baby spinach" "Greek yogurt"
python main.py --locale "90210" --items "avocados" "organic milk" --max-stores 8
python main.py --locale "Chicago, IL" --items "ribeye steak" --output-json report.json
python main.py                      # interactive mode — prompts for location/items
```

### Web UI

```bash
python web.py            # → http://127.0.0.1:8000
python web.py --port 9000
```

A single-page form (location, items, max stores, concurrency) that runs the same
pipeline, streams live progress, and renders the report in the browser — same
verification flags, unit-grouped tables, and data-quality footer as the CLI.

| Flag | Default | Description |
|---|---|---|
| `--locale`, `-l` | — | City/state or zip code |
| `--items`, `-i` | — | Items to compare |
| `--max-stores`, `-m` | 6 | Max stores to search |
| `--max-concurrency` | 4 | Concurrent price-scout agent runs |
| `--output-json PATH` | — | Save the full structured report as JSON |

## Example Output (from a real run)

```
✅ Found 4 stores: H-E-B (Hancock Center), H-E-B (Mueller), Central Market (North Lamar), ...
📊 'cherry tomatoes': 13 listings collected (13 price-verified)

╭──────────────────────────── 📋 Executive Summary ────────────────────────────╮
│  The best buy on fresh cherry tomatoes in Austin right now is the H-E-B      │
│  Texas Roots 8 oz pack at $2.98 (~$5.96/lb equivalent) ... The honest        │
│  catch: zero items are on sale anywhere — all 13 listings are everyday       │
│  prices with no % off, no weekly-ad deals.                                   │
╰──────────────────────────────────────────────────────────────────────────────╯
│ 🥇 1st │ H-E-B (Hancock Center) │ $2.98 │ $2.98 │ each │ ✅ verified │
...
│ ⚠️  Data quality: Data integrity is solid on price (13/13 verified) but      │
│ thin on scope: all four stores belong to H-E-B ...                           │
```

Note the honesty features in action: verification counts, "no sales" stated plainly instead of invented discounts, and scope caveats surfaced by the analyst itself. When search yields nothing, the report says so — it never fabricates a price table.

## Multi-Agent Architecture

```
                 ┌────────────────────────────────────┐
                 │  Orchestrator (plain async Python) │
                 │  assigns listing IDs, verifies,    │
                 │  assembles the final report        │
                 └───────┬──────────────────┬─────────┘
                         │                  │
         ┌───────────────▼──┐        ┌─────▼──────────────────────┐
         │  Store Finder    │        │  Deal Analyst (Kimi K3)     │
         │  Agent (Kimi K3) │        │  emits analysis ONLY —      │
         │                  │        │  references listings by ID, │
         │  Tools:          │        │  never re-emits prices      │
         │  • find_grocery_ │        │                             │
         │    stores        │        │  Tools:                     │
         │  • find_         │        │  • get_listings_for_item    │
         │    specialty_    │        │  • compare_items_across_    │
         │    stores        │        │    stores (unit-grouped)    │
         │  • find_big_box_ │        │  • get_store_summary        │
         │    stores        │        │  • get_all_items            │
         └──────────────────┘        │  • get_data_quality         │
                                     └─────────────────────────────┘
         ┌──────────────────────────────────────────────────────┐
         │        Price Scout Agents (Kimi K3)                  │
         │  [concurrent per store × item, semaphore-bounded]    │
         │                                                      │
         │  Tools:                 After each run:              │
         │  • search_item_price    • every price cross-checked  │
         │  • get_weekly_deals       against captured snippets  │
         │  • search_membership_   • fabrications discarded     │
         │    deals                • unverified prices flagged  │
         └──────────────────────────────────────────────────────┘
```

### Price verification (anti-hallucination)

LLM-reported prices are only as good as their sources, so the planner enforces honesty structurally:

- Every search result a scout fetches is captured; after the run, each reported price is checked against the dollar amounts actually present in those snippets (`tools/price_parser.py`). Found → **verified** ✅; not found → flagged **unverified** (`?` in the report) and slightly penalized in ranking.
- If a scout reports prices but its searches returned nothing, the listings are **discarded as fabricated**.
- The final report is assembled **in code** from the scouted listings — the Deal Analyst references listings by ID and cannot alter a price. Total savings are computed deterministically.
- Prices are ranked only within the same unit basis (weight units normalize to per-lb; a pint is never "cheaper" than a pound).
- A data-quality footer reports verification counts, scout confidence, and items with no data.

## Web Search

No API key is required: searches go through the `ddgs` multi-engine client, rotating **bing → yahoo → duckduckgo → brave** (override the order with `WEB_SEARCH_BACKENDS`). Searches are serialized and paced to avoid rate limits.

**Recommended:** set `SERPAPI_KEY` (free tier: 100 searches/month at [serpapi.com](https://serpapi.com); a full run uses ~20). With a key, SerpAPI (Google) becomes the primary engine — far more reliable and higher-quality results than scraping free engines, which throttle sustained query bursts.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MOONSHOT_API_KEY` | ✅ Yes | Your Kimi/Moonshot API key |
| `SERPAPI_KEY` | ⬜ Recommended | SerpAPI key — reliable primary search |
| `KIMI_REASONING_EFFORT` | ⬜ Optional | `none/minimal/low/medium/high/xhigh/max` (default `max`; `off` omits the parameter). Lower = faster runs |
| `WEB_SEARCH_BACKENDS` | ⬜ Optional | Comma-separated free-engine order (default `bing,yahoo,duckduckgo,brave`) |

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite (55 tests) needs **no network and no real API key** — agent flows run against pydantic-ai's `TestModel`, and `ALLOW_MODEL_REQUESTS` is disabled so no test can ever hit the Moonshot API. Covered: price math and deal sanity checks, unit normalization, the fabrication guard, report assembly, both renderers (including >30-listing tables), and the full pipeline end-to-end.

## Troubleshooting

- **Every search fails / report says "no data"** — free search engines throttle bursts and can temporarily block your IP. Add `SERPAPI_KEY` (see above), or wait ~20 minutes and rerun.
- **Runs feel slow** — `KIMI_REASONING_EFFORT=max` makes each agent think hard (a full run can take 10–20 min). Set `KIMI_REASONING_EFFORT=medium` for faster interactive runs.
- **429 "insufficient balance"** — your Moonshot account needs credit; note that platform.moonshot.**ai** and platform.moonshot.**cn** have separate billing, and this app uses the `.ai` endpoint.
- **All stores from one chain** — store discovery is search-driven and non-deterministic; raise `--max-stores` for broader coverage.

## Project Structure

```
kimi-grocery-planner/
├── main.py                    # CLI entry point + Rich/plain renderers
├── web.py                     # FastAPI web UI (form → run → HTML report)
├── templates/index.html       # The form page (self-contained, light/dark)
├── config.py                  # Kimi K3 model config (single source of truth)
├── agents/
│   ├── orchestrator.py        # Plain-Python coordinator + report assembly
│   ├── store_finder.py        # Discovers local grocery stores
│   ├── price_scout.py         # Scouts prices per store×item + verification
│   └── deal_analyst.py        # Analysis by listing-ID reference
├── models/
│   ├── store.py               # Store, MembershipProgram
│   ├── product.py             # PricePoint, ProductListing, unit normalization
│   └── report.py              # AnalystOutput (LLM) vs ShoppingReport (code-built)
├── tools/
│   ├── web_search.py          # SerpAPI + multi-engine ddgs cascade
│   └── price_parser.py        # Regex price extraction (powers verification)
├── tests/                     # pytest suite — no network, no API key
├── .env.example
├── requirements.txt
└── requirements-dev.txt
```
