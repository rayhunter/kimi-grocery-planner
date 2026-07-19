"""Tests for code-side report assembly: the analyst references, code resolves."""
from agents.orchestrator import build_report, _dedupe_stores, _fallback_best_pick
from models.report import AnalystOutput, ItemAnalysis, ShoppingTrip
from models.product import UnitType
from tests.conftest import make_listing


def _analysis(**overrides):
    defaults = dict(
        executive_summary="Buy at Kroger.",
        item_analyses=[
            ItemAnalysis(
                item_query="cherry tomatoes",
                best_listing_id="L1",
                runner_up_listing_id="L2",
                reasoning="Cheapest verified price.",
            )
        ],
        optimized_trips=[
            ShoppingTrip(primary_store="Kroger", items_to_buy_here=["cherry tomatoes"],
                         estimated_total=3.99, notes="one stop")
        ],
    )
    defaults.update(overrides)
    return AnalystOutput(**defaults)


class TestBuildReport:
    def _listings(self):
        return {
            "cherry tomatoes": [
                make_listing(store_name="Kroger", regular=4.99, sale=3.99, listing_id="L1"),
                make_listing(store_name="Target", regular=4.49, listing_id="L2"),
            ]
        }

    def test_resolves_ids_to_real_listings(self, kroger):
        report = build_report("Austin, TX", [kroger], self._listings(), {}, _analysis())
        rec = report.items_analyzed[0]
        assert rec.best_pick.listing_id == "L1"
        assert rec.best_pick.price.effective_price == 3.99  # real data, not re-emitted
        assert rec.runner_up.listing_id == "L2"

    def test_savings_computed_deterministically(self, kroger):
        report = build_report("Austin, TX", [kroger], self._listings(), {}, _analysis())
        assert report.total_potential_savings == 1.00  # 4.99 - 3.99 on the best pick

    def test_invalid_id_falls_back_deterministically(self, kroger):
        analysis = _analysis(item_analyses=[
            ItemAnalysis(item_query="cherry tomatoes", best_listing_id="L999", reasoning="?")
        ])
        report = build_report("Austin, TX", [kroger], self._listings(), {}, analysis)
        rec = report.items_analyzed[0]
        assert rec.best_pick is not None
        assert rec.best_pick.listing_id == "L1"  # lowest value_score in largest group
        assert "deterministically" in rec.data_caveats

    def test_item_with_no_listings_is_honest(self, kroger):
        listings = {"unicorn fruit": []}
        analysis = _analysis(item_analyses=[
            ItemAnalysis(item_query="unicorn fruit", best_listing_id=None,
                         reasoning="No data found.")
        ])
        report = build_report("Austin, TX", [kroger], listings, {}, analysis)
        rec = report.items_analyzed[0]
        assert rec.best_pick is None
        assert rec.all_listings == []
        assert "No price data found for: unicorn fruit" in report.data_quality_notes

    def test_unverified_counts_surface_in_quality_notes(self, kroger):
        listings = {
            "cherry tomatoes": [
                make_listing(regular=4.99, listing_id="L1", verified=False),
            ]
        }
        analysis = _analysis(item_analyses=[
            ItemAnalysis(item_query="cherry tomatoes", best_listing_id="L1", reasoning="x")
        ])
        report = build_report("Austin, TX", [kroger], listings, {}, analysis)
        assert "1/1 prices could not be verified" in report.data_quality_notes

    def test_confidence_notes_attached(self, kroger):
        report = build_report(
            "Austin, TX", [kroger], self._listings(),
            {"cherry tomatoes": "Kroger: high; Target: low"}, _analysis(),
        )
        assert report.items_analyzed[0].confidence_note == "Kroger: high; Target: low"


class TestHelpers:
    def test_dedupe_stores(self, kroger, target):
        dupe = kroger.model_copy(update={"name": "  KROGER "})
        # name dupes collapse case-insensitively, ignoring whitespace
        assert len(_dedupe_stores([kroger, target, dupe])) == 2

    def test_fallback_best_pick_prefers_largest_unit_group(self):
        listings = [
            make_listing(regular=1.99, unit=UnitType.PINT),        # cheapest overall, minority basis
            make_listing(regular=3.99, unit=UnitType.PER_LB),
            make_listing(regular=2.99, unit=UnitType.PER_LB),
        ]
        pick = _fallback_best_pick(listings)
        assert pick.price.regular_price == 2.99  # best within per_lb (the largest group)

    def test_fallback_best_pick_empty(self):
        assert _fallback_best_pick([]) is None
