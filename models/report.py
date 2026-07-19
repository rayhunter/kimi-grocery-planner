from __future__ import annotations
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from models.product import ProductListing
from models.store import Store


# ── Analyst output (what the LLM emits) ─────────────────────────────────────
# The Deal Analyst references listings by their code-assigned listing_id
# instead of re-emitting price data, so prices can never be silently altered
# between scouting and the final report.

class ItemAnalysis(BaseModel):
    item_query: str = Field(description="The user's search term this analysis is for")
    best_listing_id: str | None = Field(
        default=None,
        description="listing_id of the best-value listing, or null if no usable data was found",
    )
    runner_up_listing_id: str | None = Field(default=None)
    reasoning: str = Field(description="Why this is the best pick (or why no pick could be made)")
    membership_tip: str | None = Field(
        default=None,
        description="Tip if a membership would unlock better savings",
    )
    price_trend: str | None = Field(
        default=None,
        description="Brief note on whether this is a good time to buy",
    )
    data_caveats: str | None = Field(
        default=None,
        description="Honesty note: low confidence, unverified prices, missing stores, etc.",
    )


class ShoppingTrip(BaseModel):
    """Optimized shopping trip — which store to go to for the best basket price."""
    primary_store: str
    items_to_buy_here: list[str]
    estimated_total: float
    notes: str


class AnalystOutput(BaseModel):
    """Direct output of the Deal Analyst agent: analysis only, no price data."""
    executive_summary: str = Field(
        description="Plain-English summary of key findings and savings opportunities"
    )
    item_analyses: list[ItemAnalysis]
    optimized_trips: list[ShoppingTrip] = Field(
        default_factory=list,
        description="Smart routing: minimize stores while maximizing savings",
    )
    overall_data_notes: str | None = Field(
        default=None,
        description="Overall data-quality caveats the shopper should know",
    )


# ── Final report (assembled in code by the orchestrator) ────────────────────

class ItemRecommendation(BaseModel):
    item_query: str = Field(description="What the user searched for")
    best_pick: ProductListing | None = Field(
        default=None,
        description="Best overall value listing; None when no usable price data was found",
    )
    runner_up: ProductListing | None = Field(default=None)
    all_listings: list[ProductListing] = Field(default_factory=list)
    reasoning: str = Field(description="Why this is the best pick")
    membership_tip: str | None = None
    price_trend: str | None = None
    data_caveats: str | None = None
    confidence_note: str | None = Field(
        default=None,
        description="Per-store scout confidence summary for this item",
    )


class ShoppingReport(BaseModel):
    locale: str = Field(description="City/zip searched")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stores_searched: list[Store]
    items_analyzed: list[ItemRecommendation]
    optimized_trips: list[ShoppingTrip] = Field(default_factory=list)
    executive_summary: str
    total_potential_savings: float = Field(
        default=0.0,
        description="Sum of deal savings (regular minus effective price) across best picks",
    )
    data_quality_notes: str | None = Field(
        default=None,
        description="Overall caveats: unverified prices, items with no data, etc.",
    )
