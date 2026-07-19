#!/usr/bin/env python3
"""
🛒 Kimi K3 Multi-Agent Grocery Shopping Planner
================================================
Find the best prices for groceries across all stores near you,
including weekly deals, membership discounts, and percentage-off offers.

Usage:
    python main.py "Austin, TX" "cherry tomatoes" "broccoli"
    python main.py --locale "Seattle, WA" --items "salmon fillet" "baby spinach" "Greek yogurt"
    python main.py --locale "94102" --items "avocados" --max-stores 8

Environment variables:
    MOONSHOT_API_KEY       (required) — Your Kimi/Moonshot API key
    SERPAPI_KEY            (optional) — SerpAPI key for richer web search results
    KIMI_REASONING_EFFORT  (optional) — reasoning effort (default "max"; "off" to omit)
"""
from __future__ import annotations
import asyncio
import sys
import os
import argparse
from dotenv import load_dotenv

# Rich terminal UI
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    from rich.rule import Rule
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from agents.orchestrator import run_shopping_planner
from models.report import ShoppingReport, ItemRecommendation
from models.product import DealType, ProductListing

load_dotenv()

console = Console() if HAS_RICH else None


# ── Formatting Helpers ─────────────────────────────────────────────────────────

DEAL_EMOJI = {
    DealType.WEEKLY_SALE: "🔖",
    DealType.PERCENT_OFF: "💯",
    DealType.MEMBERSHIP: "🎟️",
    DealType.BUY_X_GET_Y: "🛍️",
    DealType.DIGITAL_COUPON: "📱",
    DealType.CLEARANCE: "🏷️",
    DealType.LOYALTY: "⭐",
    DealType.REGULAR: "  ",
}

UNIT_DISPLAY = {
    "each": "each",
    "per_lb": "/lb",
    "per_oz": "/oz",
    "per_kg": "/kg",
    "bunch": "bunch",
    "package": "pkg",
    "pint": "pint",
}


def deal_badge(deal_type: str) -> str:
    try:
        return DEAL_EMOJI[DealType(deal_type)]
    except (ValueError, KeyError):
        return "  "


def rank_label(i: int, plain: bool = False) -> str:
    if plain:
        return f"#{i + 1}"
    medals = ["🥇 1st", "🥈 2nd", "🥉 3rd"]
    return medals[i] if i < len(medals) else f"  #{i + 1}"


def unit_display(unit_value: str) -> str:
    return UNIT_DISPLAY.get(unit_value, unit_value)


def grouped_listings(rec: ItemRecommendation) -> list[tuple[str, list[ProductListing]]]:
    """Listings grouped by comparable unit basis (largest group first),
    each group sorted best-value first. Ranks are only meaningful within a group."""
    groups: dict[str, list[ProductListing]] = {}
    for listing in rec.all_listings:
        groups.setdefault(listing.comparison_unit, []).append(listing)
    return [
        (unit, sorted(group, key=lambda x: x.value_score))
        for unit, group in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    ]


def render_report_rich(report: ShoppingReport):
    """Render the shopping report with Rich formatting."""
    # Header
    console.print()
    console.print(Rule(f"[bold cyan]🛒 Grocery Price Report — {report.locale}[/bold cyan]", style="cyan"))
    console.print(f"[dim]Generated: {report.generated_at.strftime('%B %d, %Y at %I:%M %p %Z')}[/dim]")
    console.print()

    # Executive Summary
    console.print(Panel(
        f"[white]{report.executive_summary}[/white]",
        title="[bold yellow]📋 Executive Summary[/bold yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))
    console.print()

    # Per-item price comparison tables
    for rec in report.items_analyzed:
        if not rec.all_listings:
            console.print(Panel(
                f"[yellow]❓ No price data found for [bold]{rec.item_query}[/bold].[/yellow]\n"
                f"[white]{rec.reasoning}[/white]",
                border_style="yellow",
            ))
            console.print()
            continue

        table = Table(
            title=f"[bold]🥬 {rec.item_query.title()}[/bold]",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
            border_style="magenta",
            expand=False,
        )
        table.add_column("Rank", justify="center", width=6)
        table.add_column("Store", min_width=22)
        table.add_column("Regular", justify="right", min_width=10)
        table.add_column("Best Price", justify="right", min_width=12)
        table.add_column("Unit", min_width=8)
        table.add_column("Deal", min_width=22)
        table.add_column("Savings", justify="right", min_width=10)

        groups = grouped_listings(rec)
        for group_idx, (unit, listings) in enumerate(groups):
            if group_idx > 0:
                table.add_section()
            for i, listing in enumerate(listings):
                p = listing.price
                deal_info = ""
                if p.deal_description:
                    deal_info = p.deal_description[:30]
                elif p.deal_type != DealType.REGULAR:
                    deal_info = p.deal_type.value.replace("_", " ").title()
                if p.membership_required:
                    deal_info += f"\n[dim]({p.membership_required})[/dim]"
                if p.percent_off:
                    deal_info += f" [green]{p.percent_off:.0f}% off[/green]"

                savings_str = f"[green]-${p.savings:.2f}[/green]" if p.savings > 0 else ""
                price_str = (
                    f"[bold green]${p.effective_price:.2f}[/bold green]"
                    if p.sale_price else f"${p.effective_price:.2f}"
                )
                if not listing.price_verified:
                    price_str += " [dim yellow]?[/dim yellow]"

                table.add_row(
                    rank_label(i),
                    listing.store_name,
                    f"${p.regular_price:.2f}",
                    price_str,
                    unit_display(p.unit.value),
                    deal_badge(p.deal_type.value) + " " + deal_info,
                    savings_str,
                )

        console.print(table)
        if len(groups) > 1:
            console.print("[dim]  Sections separate unit bases (e.g. /lb vs pkg) — ranks apply within a section.[/dim]")
        if any(not l.price_verified for l in rec.all_listings):
            console.print("[dim yellow]  ? = price not verified against raw search sources[/dim yellow]")

        # Best pick callout (or honest gap)
        best = rec.best_pick
        if best:
            body = (
                f"[bold green]✅ Best pick: {best.store_name}[/bold green] — "
                f"${best.price.effective_price:.2f} {unit_display(best.price.unit.value)}"
                + ("" if best.price_verified else " [yellow](unverified)[/yellow]")
                + f"\n[white]{rec.reasoning}[/white]"
                + (f"\n\n[yellow]💡 Membership tip: {rec.membership_tip}[/yellow]" if rec.membership_tip else "")
                + (f"\n[dim]⚠️  {rec.data_caveats}[/dim]" if rec.data_caveats else "")
            )
            console.print(Panel(body, border_style="green", padding=(0, 2)))
        else:
            console.print(Panel(
                f"[yellow]No confident pick for this item.[/yellow]\n[white]{rec.reasoning}[/white]",
                border_style="yellow", padding=(0, 2),
            ))
        console.print()

    # Optimized shopping trips
    if report.optimized_trips:
        console.print(Rule("[bold blue]🗺️  Optimized Shopping Trips[/bold blue]", style="blue"))
        console.print()
        for i, trip in enumerate(report.optimized_trips, 1):
            items_str = ", ".join(trip.items_to_buy_here)
            console.print(Panel(
                f"[bold]Stop {i}: {trip.primary_store}[/bold]\n"
                f"Buy: [cyan]{items_str}[/cyan]\n"
                f"Est. total: [bold green]${trip.estimated_total:.2f}[/bold green]\n"
                f"[dim]{trip.notes}[/dim]",
                border_style="blue",
            ))
        console.print()

    # Savings + data quality footer
    footer = (
        f"[bold]💰 Total Deal Savings: [green]${report.total_potential_savings:.2f}[/green][/bold] "
        f"[dim](regular vs. effective price at the recommended stores)[/dim]\n"
        f"[dim]Stores searched: {', '.join(s.name for s in report.stores_searched)}[/dim]"
    )
    if report.data_quality_notes:
        footer += f"\n[yellow]⚠️  Data quality: {report.data_quality_notes}[/yellow]"
    console.print(Panel(footer, border_style="cyan"))
    console.print()


def render_report_plain(report: ShoppingReport):
    """Plain text fallback for environments without Rich."""
    print(f"\n{'='*60}")
    print(f"GROCERY PRICE REPORT — {report.locale}")
    print(f"Generated: {report.generated_at.strftime('%B %d, %Y at %I:%M %p %Z')}")
    print(f"{'='*60}\n")
    print("SUMMARY")
    print("-" * 40)
    print(report.executive_summary)
    print()

    for rec in report.items_analyzed:
        print(f"\n{rec.item_query.upper()}")
        print("-" * 40)
        if not rec.all_listings:
            print(f"  No price data found. {rec.reasoning}")
            continue
        for unit, listings in grouped_listings(rec):
            print(f"  [basis: {unit_display(unit)}]")
            for i, listing in enumerate(listings):
                p = listing.price
                deal = f" [{p.deal_type.value}]" if p.deal_type != DealType.REGULAR else ""
                membership = f" (requires: {p.membership_required})" if p.membership_required else ""
                savings = f" Save ${p.savings:.2f}" if p.savings > 0 else ""
                verified = "" if listing.price_verified else " (unverified)"
                print(
                    f"  {rank_label(i, plain=True)} {listing.store_name}: "
                    f"${p.effective_price:.2f} {unit_display(p.unit.value)}"
                    f"{deal}{membership}{savings}{verified}"
                )
        if rec.best_pick:
            print(f"\nBest: {rec.best_pick.store_name} — {rec.reasoning}")
        else:
            print(f"\nNo confident pick. {rec.reasoning}")
        if rec.data_caveats:
            print(f"Caveats: {rec.data_caveats}")

    print(f"\n{'='*60}")
    print(f"Total deal savings (regular vs. effective at recommended stores): "
          f"${report.total_potential_savings:.2f}")
    if report.data_quality_notes:
        print(f"Data quality: {report.data_quality_notes}")
    print(f"{'='*60}\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="🛒 Kimi K3 Multi-Agent Grocery Price Planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "positional_items",
        nargs="*",
        help="Items to compare (positional, used if --items not specified)",
    )
    parser.add_argument(
        "--locale", "-l",
        default=None,
        help="Location to search (city, state or zip code)",
    )
    parser.add_argument(
        "--items", "-i",
        nargs="+",
        help="Grocery items to compare prices for",
    )
    parser.add_argument(
        "--max-stores", "-m",
        type=int,
        default=6,
        help="Maximum number of stores to search (default: 6)",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=4,
        help="Maximum concurrent price-scout runs (default: 4)",
    )
    parser.add_argument(
        "--output-json",
        help="Save the full report as JSON to this file path",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    # Handle first positional arg as locale if --locale not given
    locale = args.locale
    items = args.items or []

    if not locale and args.positional_items:
        locale = args.positional_items[0]
        if not items:
            items = args.positional_items[1:]

    # Interactive fallback
    if not locale:
        if HAS_RICH:
            console.print("[bold cyan]🛒 Kimi K3 Grocery Shopping Planner[/bold cyan]")
            console.print("[dim]Powered by Pydantic AI + Kimi K3[/dim]\n")
        locale = input("📍 Enter your location (city, state or zip): ").strip()

    if not items:
        raw = input("🛒 Items to compare (comma-separated): ").strip()
        items = [i.strip() for i in raw.split(",") if i.strip()]

    if not locale or not items:
        print("❌ Please provide a location and at least one item.")
        sys.exit(1)

    # Check API key
    if not os.environ.get("MOONSHOT_API_KEY"):
        print("❌ MOONSHOT_API_KEY environment variable is not set.")
        print("   Get your key at: https://platform.moonshot.ai")
        print("   Then: export MOONSHOT_API_KEY=your_key_here")
        sys.exit(1)

    if HAS_RICH:
        console.print(f"\n[bold cyan]🛒 Kimi K3 Grocery Shopping Planner[/bold cyan]")
        console.print(f"[dim]Powered by Pydantic AI + Kimi K3 (2.8T params, 1M context)[/dim]\n")
        console.print(f"📍 Location: [bold]{locale}[/bold]")
        console.print(f"🛒 Items:    [bold]{', '.join(items)}[/bold]")
        console.print(f"🏪 Max stores: [bold]{args.max_stores}[/bold]\n")
    else:
        print(f"\nKimi K3 Grocery Shopping Planner")
        print(f"Location: {locale}")
        print(f"Items: {', '.join(items)}")
        print()

    def on_progress(msg: str):
        if HAS_RICH:
            console.print(f"  {msg}")
        else:
            print(f"  {msg}")

    # Run the multi-agent planner
    try:
        report = await run_shopping_planner(
            locale=locale,
            items=items,
            max_stores=args.max_stores,
            max_concurrency=args.max_concurrency,
            on_progress=on_progress,
        )
    except Exception as e:
        if HAS_RICH:
            console.print(f"\n[bold red]❌ Error: {e}[/bold red]")
        else:
            print(f"\n❌ Error: {e}")
        raise

    # Render the report
    if HAS_RICH:
        render_report_rich(report)
    else:
        render_report_plain(report)

    # Optional JSON export
    if args.output_json:
        with open(args.output_json, "w") as f:
            f.write(report.model_dump_json(indent=2))
        if HAS_RICH:
            console.print(f"[dim]📁 Full report saved to: {args.output_json}[/dim]")
        else:
            print(f"Report saved to: {args.output_json}")


if __name__ == "__main__":
    asyncio.run(main())
