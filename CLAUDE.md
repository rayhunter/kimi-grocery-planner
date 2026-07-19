# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A CLI multi-agent grocery price comparison tool built on **Pydantic AI** (pinned `2.12.0`) with **Kimi K3** (Moonshot's model, accessed via their OpenAI-compatible API). Given a location and a list of grocery items, it discovers nearby stores, scouts prices/deals via web search, and produces a ranked shopping report with per-price verification against sources.

## Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set MOONSHOT_API_KEY (required); SERPAPI_KEY, KIMI_REASONING_EFFORT optional

# Run (CLI)
python main.py "Austin, TX" "cherry tomatoes" "broccoli"
python main.py --locale "94102" --items "avocados" --max-stores 8 --max-concurrency 4 --output-json report.json
python main.py   # interactive mode, prompts for location/items

# Run (web UI) — serves a form page at http://127.0.0.1:8000
python web.py [--port 9000]

# Tests (no network, no real API key — agents run against pydantic-ai TestModel)
pip install -r requirements-dev.txt
python -m pytest                       # whole suite
python -m pytest tests/test_agent_flows.py -k pipeline   # single test
```

There are no linters or build steps configured. This is not a git repository.

## Architecture

The pipeline is a three-phase flow driven by `agents/orchestrator.py::run_shopping_planner()` — the orchestrator is **plain async Python, not an LLM agent**; only the three sub-agents call Kimi K3:

1. **Store Finder** (`agents/store_finder.py`) — one agent run per query; returns structured `Store` objects with membership programs. Results are deduped by name and capped at `max_stores`.
2. **Price Scout** (`agents/price_scout.py`) — one agent run per (store × item) combination, run concurrently under an `asyncio.Semaphore(max_concurrency)`. Individual failures are swallowed (logged via progress callback) so one bad scout doesn't kill the run.
3. **Deal Analyst** (`agents/deal_analyst.py`) — single agent run that reads listings through its tools and emits `AnalystOutput`: analysis that **references listings by `listing_id`** — it never re-emits price data.

### The anti-fabrication design (do not regress this)

The report's integrity rests on three mechanisms; changes must preserve all of them:

- **Snippet capture + verification** (`agents/price_scout.py`): every tool call appends its raw search results to `PriceScoutDeps.captured_snippets`. After the run, `verify_scout_result()` cross-checks each reported price against dollar amounts extracted from those snippets (`tools/price_parser.py::extract_prices`). Matching prices → `price_verified=True`; no snippets at all but listings reported → listings discarded, confidence forced to `"none"`.
- **Code-assembled report** (`agents/orchestrator.py::build_report`): the orchestrator assigns `listing_id`s (`L1`, `L2`, …) after scouting, the analyst picks by ID, and `build_report()` resolves IDs back to the *original* listing objects. Invalid/missing IDs fall back to a deterministic pick (`_fallback_best_pick`: best `value_score` within the largest comparable-unit group). `total_potential_savings` is computed in code (sum of best-pick `regular − effective`), never by the LLM.
- **Unit-aware comparison** (`models/product.py`): `ProductListing.comparison_unit` buckets weight units (per_lb/per_oz/per_kg, normalized to per-lb via `price_per_lb`) separately from count/volume units. `value_score` is only meaningful **within** a bucket — every ranking site (analyst tools, `_fallback_best_pick`, both renderers via `main.py::grouped_listings`) groups before sorting. Membership-gated prices get a 1.05× penalty, unverified prices 1.02×.

### Agent pattern (consistent across all three agents)

- Module-level `Agent(get_kimi_model(), output_type=<pydantic model>, deps_type=..., system_prompt=..., model_settings=kimi_model_settings())`.
- Tools via `@agent.tool` with `RunContext[DepsDataclass]` (deps are dataclasses in the same file); Store Finder uses `@agent.tool_plain` since it needs no deps.
- A public `async def` wrapper at the bottom of each file (`find_stores_near`, `scout_prices`, `analyze_deals`) is the only thing other modules import; wrappers return `result.output` (current pydantic-ai API — not the removed `result.data`).
- Agents are instantiated at **import time**, so importing any agent module requires `MOONSHOT_API_KEY` to be set (tests set a dummy in `tests/conftest.py` before imports and set `ALLOW_MODEL_REQUESTS = False`).

### Model/config plumbing

`config.py` is the single source of truth: `get_kimi_model()` is `@lru_cache`d, returning an `OpenAIChatModel("kimi-k3", provider=OpenAIProvider(base_url=..., api_key=...))`. `kimi_model_settings()` reads `KIMI_REASONING_EFFORT` (default `"max"`, `"off"` omits the setting). Importing `config` runs `load_dotenv()` — this ordering matters because agents are built at import time.

### Data models (`models/`)

- `product.py` — `PricePoint` has a sanity validator (drops "sale" prices ≥ regular and implausible percent_off) and `effective_price` honors `percent_off` when no explicit sale price. `ProductListing` carries `listing_id` and `price_verified`.
- `report.py` — two layers: `AnalystOutput`/`ItemAnalysis` (what the LLM emits, ID references only) vs `ShoppingReport`/`ItemRecommendation` (assembled in code; `best_pick` is `None` when no data — renderers must handle that honestly).

### Web search (`tools/web_search.py`)

Cascade: `ddgs` (DuckDuckGo client, primary) → SerpAPI (if `SERPAPI_KEY` set and results thin) → regex-scraping DuckDuckGo HTML (last resort). A module-level `Semaphore(4)` bounds all outbound searches; weekly-ad lookups are cached per (store, city) since all items share one weekly ad. `format_results_for_llm()` frames results as untrusted data (prompt-injection hedge) — all agent tools must return search results through it.

### CLI (`main.py`)

Argument handling supports positional (`locale item1 item2...`) and flag styles, falling back to interactive prompts. Rendering has **three** paths — Rich, plain text, and the web UI's HTML (`web.py::render_report_html`) — **any output change must update all three**, and each must keep handling: mixed unit groups (sections), `best_pick is None`, unverified markers (`?` / `(unverified)`), and the data-quality footer. Rank labels come from `rank_label(i)` (safe for any index — do not reintroduce fixed-size label lists).

### Web UI (`web.py` + `templates/index.html`)

FastAPI app sharing the CLI's pipeline: `POST /api/runs` starts `run_shopping_planner` as an asyncio task (in-memory `RUNS` registry, capped at 20), `GET /api/runs/{id}` returns progress lines + server-rendered `report_html` when complete; the page polls every 2s. `render_report_html` reuses `grouped_listings`/`unit_display`/`rank_label` from `main.py` and escapes all model/web-sourced text with `html.escape`. The template is self-contained (inline CSS/JS, light/dark via `prefers-color-scheme`).
