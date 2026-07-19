"""
Orchestrator — plain async Python (not an LLM agent) that coordinates the
three sub-agents and assembles the final report.

The report is built HERE, in code, from the listings the scouts actually
collected. The Deal Analyst only contributes analysis (picks by listing_id,
reasoning, trips, summary), so price data cannot be altered by the model
between scouting and rendering.
"""
from __future__ import annotations
import asyncio
from collections.abc import Callable

from models.store import Store
from models.product import ProductListing
from models.report import AnalystOutput, ItemRecommendation, ShoppingReport
from agents.store_finder import find_stores_near
from agents.price_scout import scout_prices
from agents.deal_analyst import analyze_deals


def _dedupe_stores(stores: list[Store]) -> list[Store]:
    seen: set[str] = set()
    unique: list[Store] = []
    for s in stores:
        key = s.name.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _fallback_best_pick(listings: list[ProductListing]) -> ProductListing | None:
    """Deterministic best pick when the analyst gives no usable listing_id:
    best value_score within the largest comparable-unit group."""
    if not listings:
        return None
    groups: dict[str, list[ProductListing]] = {}
    for l in listings:
        groups.setdefault(l.comparison_unit, []).append(l)
    largest = max(groups.values(), key=len)
    return min(largest, key=lambda x: x.value_score)


def build_report(
    locale: str,
    stores: list[Store],
    all_listings: dict[str, list[ProductListing]],
    confidence_notes: dict[str, str],
    analysis: AnalystOutput,
) -> ShoppingReport:
    """Assemble the final report from real listings + the analyst's analysis."""
    by_id: dict[str, ProductListing] = {
        l.listing_id: l for listings in all_listings.values() for l in listings if l.listing_id
    }
    analyses_by_item = {a.item_query: a for a in analysis.item_analyses}

    recommendations: list[ItemRecommendation] = []
    for item, listings in all_listings.items():
        a = analyses_by_item.get(item)
        best = by_id.get(a.best_listing_id) if a and a.best_listing_id else None
        runner_up = by_id.get(a.runner_up_listing_id) if a and a.runner_up_listing_id else None
        caveats = a.data_caveats if a else None
        if best is None and listings:
            best = _fallback_best_pick(listings)
            note = "Best pick chosen deterministically (analyst reference was missing/invalid)."
            caveats = f"{caveats} {note}".strip() if caveats else note
        recommendations.append(
            ItemRecommendation(
                item_query=item,
                best_pick=best,
                runner_up=runner_up if runner_up is not best else None,
                all_listings=listings,
                reasoning=a.reasoning if a else "No analysis produced for this item.",
                membership_tip=a.membership_tip if a else None,
                price_trend=a.price_trend if a else None,
                data_caveats=caveats,
                confidence_note=confidence_notes.get(item),
            )
        )

    # Deterministic savings: deal savings (regular − effective) of each best pick.
    total_savings = sum(r.best_pick.price.savings for r in recommendations if r.best_pick)

    total = sum(len(v) for v in all_listings.values())
    unverified = sum(1 for v in all_listings.values() for l in v if not l.price_verified)
    quality_bits = []
    if analysis.overall_data_notes:
        quality_bits.append(analysis.overall_data_notes)
    if unverified:
        quality_bits.append(
            f"{unverified}/{total} prices could not be verified against raw search "
            f"sources and may be inaccurate."
        )
    no_data = [r.item_query for r in recommendations if not r.all_listings]
    if no_data:
        quality_bits.append(f"No price data found for: {', '.join(no_data)}.")

    return ShoppingReport(
        locale=locale,
        stores_searched=stores,
        items_analyzed=recommendations,
        optimized_trips=analysis.optimized_trips,
        executive_summary=analysis.executive_summary,
        total_potential_savings=round(total_savings, 2),
        data_quality_notes=" ".join(quality_bits) or None,
    )


async def run_shopping_planner(
    locale: str,
    items: list[str],
    max_stores: int = 6,
    max_concurrency: int = 4,
    on_progress: Callable[[str], None] | None = None,
) -> ShoppingReport:
    """
    Main entry point for the multi-agent grocery shopping planner.

    Args:
        locale: City, zip code, or neighborhood (e.g. "Austin, TX" or "94102")
        items: List of grocery items to compare (e.g. ["cherry tomatoes", "broccoli"])
        max_stores: Maximum number of stores to search (limits API calls)
        max_concurrency: Maximum concurrent price-scout agent runs
        on_progress: Optional callback fn(message: str) for progress updates

    Returns:
        ShoppingReport with best prices, deals, and shopping recommendations
    """
    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    # ── PHASE 1: Discover stores ───────────────────────────────────────────
    progress(f"🔍 Discovering grocery stores near {locale}...")
    store_result = await find_stores_near(locale)
    stores = _dedupe_stores(store_result.stores)[:max_stores]
    progress(f"✅ Found {len(stores)} stores: {', '.join(s.name for s in stores)}")

    # ── PHASE 2: Scout prices concurrently (bounded) ───────────────────────
    all_listings: dict[str, list[ProductListing]] = {item: [] for item in items}
    scout_notes: dict[str, list[str]] = {item: [] for item in items}
    semaphore = asyncio.Semaphore(max_concurrency)

    async def scout_one(store: Store, item: str):
        async with semaphore:
            progress(f"💰 Scouting '{item}' at {store.display_name}...")
            try:
                result = await scout_prices(store, item, locale)
                scout_notes[item].append(f"{store.display_name}: {result.confidence}")
                return item, result.listings
            except Exception as e:
                progress(f"⚠️  Could not get prices for '{item}' at {store.name}: {e}")
                scout_notes[item].append(f"{store.display_name}: failed ({type(e).__name__})")
                return item, []

    tasks = [scout_one(store, item) for store in stores for item in items]
    for item, listings in await asyncio.gather(*tasks):
        all_listings[item].extend(listings)

    # Assign stable ids the analyst will use to reference listings.
    counter = 0
    for listings in all_listings.values():
        for listing in listings:
            counter += 1
            listing.listing_id = f"L{counter}"

    confidence_notes = {item: "; ".join(notes) for item, notes in scout_notes.items()}
    for item, listings in all_listings.items():
        verified = sum(1 for l in listings if l.price_verified)
        progress(f"📊 '{item}': {len(listings)} listings collected ({verified} price-verified)")

    # ── PHASE 3: Analyze and assemble report ───────────────────────────────
    progress("🧠 Kimi K3 analyzing deals and optimizing your shopping trip...")
    analysis = await analyze_deals(locale, stores, all_listings, confidence_notes)
    report = build_report(locale, stores, all_listings, confidence_notes, analysis)
    progress("✅ Shopping report ready!")

    return report
