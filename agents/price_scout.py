"""
Price Scout Agent — finds current prices, weekly deals, membership deals,
and percentage-off promotions for specific items at specific stores.

Every search result fetched during a run is captured in the run's deps.
After the run, each reported price is cross-checked against the captured
snippets (tools/price_parser.py): prices that actually appear in the raw
sources are marked price_verified; if the agent reports listings but no
snippets were ever fetched, the listings are discarded as fabricated.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from config import get_kimi_model, kimi_model_settings
from models.store import Store
from models.product import ProductListing
from tools.web_search import search_store_prices, search_weekly_deals, format_results_for_llm
from tools.price_parser import extract_prices


@dataclass
class PriceScoutDeps:
    store: Store
    item_query: str
    city: str
    captured_snippets: list[dict] = field(default_factory=list)


class PriceScoutResult(BaseModel):
    listings: list[ProductListing] = Field(
        default_factory=list,
        description="Price listings actually found. MUST be empty if no price data was found.",
    )
    confidence: str = Field(description="high/medium/low/none — confidence in price accuracy")
    data_freshness: str = Field(description="Estimate of how fresh the price data is")
    raw_notes: str = Field(description="Any caveats about the data found")


price_scout_agent = Agent(
    get_kimi_model(),
    output_type=PriceScoutResult,
    deps_type=PriceScoutDeps,
    system_prompt="""You are a grocery price intelligence agent. Your mission is to find
the CURRENT price for a specific grocery item at a specific store.

You must identify:
1. **Regular (shelf) price** — the normal everyday price
2. **Sale price** — any current weekly ad deal or markdown
3. **Membership price** — price available with store loyalty card, Costco membership,
   Target Circle, Amazon Prime (for Whole Foods), etc.
4. **Percent-off deals** — digital coupons, "X% off" promotions
5. **Buy X get Y deals** — BOGO, multi-buy offers

Be specific about:
- Price per unit (each, per lb, per oz, per bunch)
- Whether the deal requires a membership/loyalty card
- Expiry dates if available
- source_url: cite the search result URL each price came from

HONESTY RULES — these override everything else:
- Report ONLY prices that appear in the search results your tools return.
- NEVER invent, estimate, or recall prices from memory. Every reported price
  is cross-checked against the raw search results; fabricated prices are
  discarded and hurt the report.
- If the search results contain no usable price, return an EMPTY listings
  list with confidence "none" and explain what you found in raw_notes.
  An honest empty result is far more valuable than a plausible guess.""",
    model_settings=kimi_model_settings(),
)


def _capture(ctx: RunContext[PriceScoutDeps], results: list[dict]) -> None:
    ctx.deps.captured_snippets.extend(results)


@price_scout_agent.tool
async def search_item_price(ctx: RunContext[PriceScoutDeps], search_query: str) -> str:
    """Search the web for the current price of an item at the target store."""
    results = await search_store_prices(ctx.deps.store.name, search_query, ctx.deps.city)
    _capture(ctx, results)
    return format_results_for_llm(results)


@price_scout_agent.tool
async def get_weekly_deals(ctx: RunContext[PriceScoutDeps]) -> str:
    """Fetch this week's sale/deal ads for the target store."""
    results = await search_weekly_deals(ctx.deps.store.name, ctx.deps.city)
    _capture(ctx, results)
    return format_results_for_llm(results)


@price_scout_agent.tool
async def search_membership_deals(ctx: RunContext[PriceScoutDeps], item: str) -> str:
    """Search for membership/loyalty-card deals for a specific item at the store."""
    store = ctx.deps.store
    membership_names = [p.name for p in store.membership_programs]
    membership_str = " OR ".join(membership_names) if membership_names else "member price loyalty card"
    results = await search_store_prices(store.name, f"{item} {membership_str}", ctx.deps.city)
    _capture(ctx, results)
    return format_results_for_llm(results)


# ── Post-run verification ──────────────────────────────────────────────────

def _snippet_prices(snippets: list[dict]) -> set[float]:
    """All dollar amounts that appear anywhere in the captured search results."""
    combined = " ".join(f"{s.get('title', '')} {s.get('snippet', '')}" for s in snippets)
    return set(extract_prices(combined))


def verify_scout_result(result: PriceScoutResult, deps: PriceScoutDeps) -> PriceScoutResult:
    """
    Cross-check reported prices against the raw snippets the agent actually saw.

    - No snippets captured but listings reported → all fabricated: drop them.
    - A listing whose regular or sale price appears in the snippets is marked
      price_verified; others are kept but flagged unverified.
    """
    if not result.listings:
        return result

    if not deps.captured_snippets:
        return PriceScoutResult(
            listings=[],
            confidence="none",
            data_freshness=result.data_freshness,
            raw_notes=(
                "Discarded model-reported listings: no search results were fetched "
                "during this run, so the prices could not have come from sources. "
                + result.raw_notes
            ),
        )

    seen_prices = _snippet_prices(deps.captured_snippets)
    for listing in result.listings:
        # Normalize store naming so downstream grouping is consistent.
        listing.store_name = deps.store.display_name
        listing.product_query = deps.item_query
        p = listing.price
        listing.price_verified = (
            round(p.regular_price, 2) in seen_prices
            or (p.sale_price is not None and round(p.sale_price, 2) in seen_prices)
        )

    unverified = sum(1 for l in result.listings if not l.price_verified)
    if unverified:
        result.raw_notes = (
            f"{unverified}/{len(result.listings)} listed price(s) do not appear in the "
            f"raw search snippets and are marked unverified. " + result.raw_notes
        )
    return result


async def scout_prices(store: Store, item_query: str, city: str) -> PriceScoutResult:
    """Public API: Scout all prices for an item at a specific store."""
    deps = PriceScoutDeps(store=store, item_query=item_query, city=city)
    prompt = (
        f"Find the current price for '{item_query}' at {store.display_name} in {city}.\n"
        f"Store type: {store.store_type.value}\n"
        f"Membership programs: {[p.name for p in store.membership_programs]}\n\n"
        f"Search for: regular price, any active weekly sale, any membership/loyalty discount, "
        f"any percentage-off coupons or digital deals. "
        f"Return all price variants you find with full deal details. "
        f"Remember: only prices present in the search results; empty listings if none found."
    )
    result = await price_scout_agent.run(prompt, deps=deps)
    return verify_scout_result(result.output, deps)
