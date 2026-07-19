"""
Store Finder Agent — discovers all grocery stores near a given locale.
Uses Kimi K3 to interpret web search results and return structured Store objects.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from config import get_kimi_model, kimi_model_settings
from models.store import Store
from tools.web_search import search_local_stores, format_results_for_llm


# ── Output Model ───────────────────────────────────────────────────────────
class StoreFinderResult(BaseModel):
    stores: list[Store]
    search_notes: str = Field(description="Notes on data quality / coverage")


# ── Agent Definition ───────────────────────────────────────────────────────
store_finder_agent = Agent(
    get_kimi_model(),
    output_type=StoreFinderResult,
    system_prompt="""You are a grocery store intelligence agent specializing in local retail.

Your job is to find ALL types of grocery stores near a given locale including:
- National chains (Kroger, Safeway, Albertsons, Publix, H-E-B, etc.)
- Warehouse/big-box stores (Costco, Sam's Club, BJ's Wholesale)
- Specialty/premium stores (Whole Foods, Sprouts, Trader Joe's, Fresh Market)
- Mass-market with grocery sections (Target, Walmart, Meijer)
- Local/regional markets and ethnic grocery stores

For each store, identify:
1. Whether they have membership programs (Costco, Sam's Club, Target Circle, Amazon Prime for Whole Foods, etc.)
2. Whether they publish weekly ads with rotating deals
3. Store type classification

Only include stores you have evidence for: either they appear in the search
results, or they are chains you are certain operate in that locale. In
search_notes, say which stores came from search evidence vs. general knowledge.""",
    model_settings=kimi_model_settings(),
)


@store_finder_agent.tool_plain
async def find_grocery_stores(locale: str) -> str:
    """Search for grocery stores near the given locale (city, zip, or neighborhood)."""
    results = await search_local_stores(locale, "grocery supermarket")
    return format_results_for_llm(results)


@store_finder_agent.tool_plain
async def find_specialty_stores(locale: str) -> str:
    """Search for specialty, local, and ethnic grocery stores near the locale."""
    results = await search_local_stores(locale, "local market specialty ethnic organic grocery store")
    return format_results_for_llm(results)


@store_finder_agent.tool_plain
async def find_big_box_stores(locale: str) -> str:
    """Search for big-box/warehouse stores (Costco, Sam's Club, Walmart, Target) near locale."""
    results = await search_local_stores(locale, "Costco Walmart Target big box wholesale grocery")
    return format_results_for_llm(results)


async def find_stores_near(locale: str) -> StoreFinderResult:
    """Public API: Find all grocery stores near a locale."""
    result = await store_finder_agent.run(
        f"Find all grocery stores, supermarkets, big-box stores, and local markets near: {locale}. "
        f"Include chain stores, warehouse clubs, specialty stores, and any local markets. "
        f"For each store, identify their membership programs and weekly ad availability."
    )
    return result.output
