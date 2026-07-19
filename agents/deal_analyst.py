"""
Deal Analyst Agent — synthesizes all price data across stores and produces
the analysis for the final shopping report.

The analyst never re-emits price data: it references listings by their
code-assigned listing_id, and the orchestrator assembles the final report
from the original listings. This means the model cannot silently alter a
price between scouting and rendering.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pydantic_ai import Agent, RunContext

from config import get_kimi_model, kimi_model_settings
from models.store import Store
from models.product import ProductListing
from models.report import AnalystOutput


@dataclass
class AnalystDeps:
    locale: str
    stores: list[Store]
    all_listings: dict[str, list[ProductListing]]  # item_query -> listings
    confidence_notes: dict[str, str] = field(default_factory=dict)  # item_query -> scout confidence summary


def _rank_label(i: int) -> str:
    return ["🥇", "🥈", "🥉"][i] if i < 3 else "  "


deal_analyst_agent = Agent(
    get_kimi_model(),
    output_type=AnalystOutput,
    deps_type=AnalystDeps,
    system_prompt="""You are a sharp, savvy grocery deal analyst.

You receive price listings for multiple items across multiple stores and your job is to:

1. **RANK** listings for each item from best to worst value
2. **HIGHLIGHT** deals that require memberships — flag when a membership would pay for itself
3. **FLAG** percentage-off deals prominently (these are time-sensitive!)
4. **OPTIMIZE** the shopping trip — suggest which 1-2 stores to hit for the best basket price
5. **ADVISE** on timing — are items on a typical sale cycle? Worth waiting?

Key rules:
- Reference listings ONLY by their listing_id (e.g. "L3") in best_listing_id /
  runner_up_listing_id. Never invent an id. If an item has no usable listings,
  set best_listing_id to null and explain in reasoning.
- Prices are only directly comparable within the same unit basis. Weight units
  are normalized to a per-lb basis for you; comparing per-lb vs per-package
  requires judgment about quantities — reason about it explicitly.
- UNVERIFIED prices did not appear in the raw search sources and may be
  model-estimated. Prefer verified prices; call out when a pick rests on an
  unverified price (use data_caveats).
- A Costco price may look cheaper but factor in the $65/year membership cost.
- Target Circle is FREE — always flag Target Circle deals as no-barrier wins.
- Whole Foods + Amazon Prime = significant savings on many items.
- Check the data-quality tool. If confidence is low or data is missing, SAY SO
  in data_caveats and overall_data_notes — don't paper over gaps.

Your executive summary should be punchy and actionable. Think like a savvy shopper, not a robot.""",
    model_settings=kimi_model_settings(),
)


def _format_listing(listing: ProductListing) -> str:
    p = listing.price
    per_lb = p.price_per_lb
    return (
        f"\n[{listing.listing_id}] {listing.store_name}"
        f"\n  Product: {listing.product_name}"
        f"\n  Regular: ${p.regular_price:.2f} {p.unit.value}"
        + (f"\n  Sale: ${p.sale_price:.2f}" if p.sale_price else "")
        + f"\n  Deal type: {p.deal_type.value}"
        + (f"\n  Membership: {p.membership_required}" if p.membership_required else "")
        + (f"\n  % Off: {p.percent_off:.0f}%" if p.percent_off else "")
        + (f"\n  Deal desc: {p.deal_description}" if p.deal_description else "")
        + f"\n  Effective price: ${p.effective_price:.2f}"
        + (f"\n  Normalized: ${per_lb:.2f}/lb" if per_lb is not None else "")
        + f"\n  Savings: ${p.savings:.2f} ({p.savings_pct:.1f}%)"
        + f"\n  Price verified in sources: {'YES' if listing.price_verified else 'NO (unverified)'}"
    )


@deal_analyst_agent.tool
async def get_listings_for_item(ctx: RunContext[AnalystDeps], item_query: str) -> str:
    """Retrieve all price listings found for a specific item across all stores."""
    listings = ctx.deps.all_listings.get(item_query, [])
    if not listings:
        return f"No listings found for '{item_query}'"
    lines = [f"=== Listings for '{item_query}' ({len(listings)} listings) ==="]
    for listing in sorted(listings, key=lambda x: (x.comparison_unit, x.value_score)):
        lines.append(_format_listing(listing))
    return "\n".join(lines)


@deal_analyst_agent.tool
async def get_all_items(ctx: RunContext[AnalystDeps]) -> str:
    """Get a list of all items that have price data available."""
    items = list(ctx.deps.all_listings.keys())
    return f"Items with pricing data: {', '.join(items)}" if items else "No items with data."


@deal_analyst_agent.tool
async def get_store_summary(ctx: RunContext[AnalystDeps]) -> str:
    """Get a summary of all stores that were searched."""
    lines = [f"=== Stores Searched ({len(ctx.deps.stores)}) ==="]
    for s in ctx.deps.stores:
        memberships = [p.name for p in s.membership_programs]
        lines.append(
            f"\n{s.display_name}"
            f"\n  Type: {s.store_type.value}"
            f"\n  Memberships: {', '.join(memberships) if memberships else 'None'}"
            f"\n  Weekly ads: {'Yes' if s.has_weekly_ad else 'Unknown'}"
        )
    return "\n".join(lines)


@deal_analyst_agent.tool
async def get_data_quality(ctx: RunContext[AnalystDeps]) -> str:
    """Per-item scout confidence and verification stats — check before finalizing."""
    lines = ["=== Data Quality ==="]
    for item, listings in ctx.deps.all_listings.items():
        verified = sum(1 for l in listings if l.price_verified)
        lines.append(
            f"\n'{item}': {len(listings)} listings, {verified} price-verified"
            + (f"\n  Scout confidence: {ctx.deps.confidence_notes[item]}"
               if item in ctx.deps.confidence_notes else "")
        )
    return "\n".join(lines)


@deal_analyst_agent.tool
async def compare_items_across_stores(ctx: RunContext[AnalystDeps], item_query: str) -> str:
    """Side-by-side price comparison for an item, grouped by comparable unit basis."""
    listings = ctx.deps.all_listings.get(item_query, [])
    if not listings:
        return f"No data to compare for '{item_query}'"

    groups: dict[str, list[ProductListing]] = {}
    for l in listings:
        groups.setdefault(l.comparison_unit, []).append(l)

    lines = [f"=== Price Comparison: {item_query} (best → worst, per unit basis) ==="]
    for unit, group in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sorted_group = sorted(group, key=lambda x: x.value_score)
        lines.append(f"\n-- Basis: {unit} ({len(group)} listings) --")
        for i, listing in enumerate(sorted_group):
            p = listing.price
            deal_tags = []
            if p.deal_type.value != "regular":
                deal_tags.append(f"[{p.deal_type.value.upper()}]")
            if p.membership_required:
                deal_tags.append(f"[{p.membership_required}]")
            if p.percent_off:
                deal_tags.append(f"[{p.percent_off:.0f}% OFF]")
            if not listing.price_verified:
                deal_tags.append("[UNVERIFIED]")
            lines.append(
                f"{_rank_label(i)} #{i + 1} [{listing.listing_id}]: {listing.store_name}"
                f"\n    → ${p.effective_price:.2f}/{p.unit.value}"
                + (f" (reg ${p.regular_price:.2f})" if p.sale_price else "")
                + (f" {' '.join(deal_tags)}" if deal_tags else "")
                + (f"\n    → Saves ${p.savings:.2f} ({p.savings_pct:.1f}%)" if p.savings > 0 else "")
            )
        if len(sorted_group) > 1:
            cheapest, priciest = sorted_group[0], sorted_group[-1]
            spread = priciest.price.effective_price - cheapest.price.effective_price
            lines.append(
                f"💡 Spread within this basis: ${spread:.2f} — {cheapest.store_name} cheapest"
            )
    if len(groups) > 1:
        lines.append(
            "\n⚠️ Multiple unit bases — cross-basis comparison requires judgment about quantities."
        )
    return "\n".join(lines)


async def analyze_deals(
    locale: str,
    stores: list[Store],
    all_listings: dict[str, list[ProductListing]],
    confidence_notes: dict[str, str] | None = None,
) -> AnalystOutput:
    """Public API: Run the deal analyst; returns analysis referencing listing ids."""
    deps = AnalystDeps(
        locale=locale,
        stores=stores,
        all_listings=all_listings,
        confidence_notes=confidence_notes or {},
    )

    items_str = ", ".join(all_listings.keys())
    prompt = (
        f"Analyze grocery prices for: {items_str}\n"
        f"Location: {locale}\n"
        f"Stores searched: {len(stores)}\n\n"
        f"Use your tools to:\n"
        f"1. Check data quality first (get_data_quality)\n"
        f"2. Compare each item across all stores\n"
        f"3. Pick the best listing for each item BY ITS listing_id\n"
        f"4. Highlight membership deals and whether they're worth it\n"
        f"5. Flag all percentage-off and weekly sale deals\n"
        f"6. Suggest the optimal shopping trip (1-2 store stops)\n"
        f"7. Write a punchy executive summary; be honest about data gaps\n\n"
        f"Be direct and actionable. A shopper should be able to read your report "
        f"and know exactly where to go and what to buy."
    )

    result = await deal_analyst_agent.run(prompt, deps=deps)
    return result.output
