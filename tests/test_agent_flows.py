"""
Agent plumbing tests using pydantic-ai's TestModel — no network, no API key.

TestModel calls every registered tool and then produces schema-valid output
(here: explicit payloads via custom_output_args), which exercises the real
tool wiring, deps threading, snippet capture, and orchestrator assembly.
"""
import pytest
from pydantic_ai.models.test import TestModel

import agents.store_finder as sf
import agents.price_scout as ps
import agents.deal_analyst as da
from agents.orchestrator import run_shopping_planner

FAKE_RESULTS = [
    {"title": "Cherry Tomatoes $3.99 at Kroger", "url": "http://kroger.com/x",
     "snippet": "Cherry tomatoes on sale $2.99 per lb this week with Kroger Plus Card"},
]


@pytest.fixture(autouse=True)
def no_real_search(monkeypatch):
    async def fake_store_prices(store_name, item, city):
        return list(FAKE_RESULTS)

    async def fake_weekly(store_name, city):
        return list(FAKE_RESULTS)

    async def fake_local(locale, store_type="grocery"):
        return [{"title": "Kroger - Austin", "url": "http://kroger.com", "snippet": "Grocery store in Austin"}]

    monkeypatch.setattr(ps, "search_store_prices", fake_store_prices)
    monkeypatch.setattr(ps, "search_weekly_deals", fake_weekly)
    monkeypatch.setattr(sf, "search_local_stores", fake_local)


STORE_ARGS = {
    "stores": [{
        "name": "Kroger", "chain": "Kroger", "store_type": "chain",
        "address": "123 Main St", "city": "Austin", "state": "TX",
        "membership_programs": [
            {"name": "Kroger Plus Card", "annual_cost": 0.0, "discount_description": "loyalty prices"}
        ],
        "has_weekly_ad": True,
    }],
    "search_notes": "from search evidence",
}

SCOUT_ARGS = {
    "listings": [{
        "store_name": "Kroger",
        "product_name": "Cherry Tomatoes 1lb",
        "product_query": "cherry tomatoes",
        "price": {
            "regular_price": 3.99, "sale_price": 2.99, "unit": "per_lb",
            "deal_type": "weekly_sale", "membership_required": "Kroger Plus Card",
        },
    }],
    "confidence": "high",
    "data_freshness": "this week",
    "raw_notes": "from weekly ad",
}

ANALYST_ARGS = {
    "executive_summary": "Kroger wins.",
    "item_analyses": [{
        "item_query": "cherry tomatoes",
        "best_listing_id": "L1",
        "reasoning": "Cheapest verified price with free loyalty card.",
    }],
    "optimized_trips": [{
        "primary_store": "Kroger", "items_to_buy_here": ["cherry tomatoes"],
        "estimated_total": 2.99, "notes": "single stop",
    }],
    "overall_data_notes": "weekly-ad data",
}


class TestIndividualAgents:
    async def test_store_finder_flow(self):
        with sf.store_finder_agent.override(model=TestModel(custom_output_args=STORE_ARGS)):
            result = await sf.find_stores_near("Austin, TX")
        assert result.stores[0].name == "Kroger"
        assert result.stores[0].membership_programs[0].name == "Kroger Plus Card"

    async def test_price_scout_flow_verifies_against_snippets(self, kroger):
        with ps.price_scout_agent.override(model=TestModel(custom_output_args=SCOUT_ARGS)):
            result = await ps.scout_prices(kroger, "cherry tomatoes", "Austin, TX")
        assert len(result.listings) == 1
        listing = result.listings[0]
        # 3.99 and 2.99 both appear in the fake snippets → verified
        assert listing.price_verified is True
        assert listing.store_name == kroger.display_name

    async def test_price_scout_discards_fabrications(self, kroger, monkeypatch):
        fabricated = {**SCOUT_ARGS, "listings": [{
            **SCOUT_ARGS["listings"][0],
            "price": {"regular_price": 9.87, "sale_price": None, "unit": "per_lb"},
        }]}
        with ps.price_scout_agent.override(model=TestModel(custom_output_args=fabricated)):
            result = await ps.scout_prices(kroger, "cherry tomatoes", "Austin, TX")
        # price 9.87 not in snippets → kept but flagged unverified
        assert result.listings[0].price_verified is False

    async def test_deal_analyst_flow(self, kroger):
        from tests.conftest import make_listing
        listings = {"cherry tomatoes": [make_listing(listing_id="L1")]}
        with da.deal_analyst_agent.override(model=TestModel(custom_output_args=ANALYST_ARGS)):
            analysis = await da.analyze_deals("Austin, TX", [kroger], listings, {"cherry tomatoes": "Kroger: high"})
        assert analysis.item_analyses[0].best_listing_id == "L1"


class TestEndToEnd:
    async def test_full_pipeline_produces_grounded_report(self):
        with (
            sf.store_finder_agent.override(model=TestModel(custom_output_args=STORE_ARGS)),
            ps.price_scout_agent.override(model=TestModel(custom_output_args=SCOUT_ARGS)),
            da.deal_analyst_agent.override(model=TestModel(custom_output_args=ANALYST_ARGS)),
        ):
            report = await run_shopping_planner(
                locale="Austin, TX",
                items=["cherry tomatoes"],
                max_stores=3,
            )

        assert report.locale == "Austin, TX"
        rec = report.items_analyzed[0]
        # The best pick is the REAL scouted listing (resolved by id), not analyst-re-emitted data.
        assert rec.best_pick is not None
        assert rec.best_pick.price.effective_price == 2.99
        assert rec.best_pick.price_verified is True
        assert rec.confidence_note and "high" in rec.confidence_note
        # Deterministic savings: 3.99 regular − 2.99 sale.
        assert report.total_potential_savings == 1.00
        assert report.optimized_trips[0].primary_store == "Kroger"

    async def test_pipeline_scrubs_pii_from_locale(self):
        """PII in the locale is stripped before any agent prompt is built."""
        progress: list[str] = []
        with (
            sf.store_finder_agent.override(model=TestModel(custom_output_args=STORE_ARGS)),
            ps.price_scout_agent.override(model=TestModel(custom_output_args=SCOUT_ARGS)),
            da.deal_analyst_agent.override(model=TestModel(custom_output_args=ANALYST_ARGS)),
        ):
            report = await run_shopping_planner(
                locale="123 Elm Street, Austin, TX",
                items=["cherry tomatoes"],
                max_stores=3,
                on_progress=progress.append,
            )

        # The street is gone from the locale that reaches the report/prompts,
        # the usable part of the location survives, and the notice names the
        # category removed without echoing the value.
        assert "Elm" not in report.locale
        assert "Austin, TX" in report.locale
        notice = next(m for m in progress if "Removed" in m)
        assert "street address" in notice and "Elm" not in notice

    async def test_pipeline_rejects_input_that_is_entirely_pii(self):
        with pytest.raises(ValueError, match="privacy scrubbing"):
            await run_shopping_planner(locale="ray@example.com", items=["milk"])
