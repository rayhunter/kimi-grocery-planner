"""Renderer tests: many listings (the old IndexError), mixed units, no-data items."""
from main import rank_label, unit_display, grouped_listings, render_report_plain, render_report_rich, HAS_RICH
from models.report import ShoppingReport, ItemRecommendation
from models.product import UnitType
from tests.conftest import make_listing


def _big_report(n_listings: int = 30) -> ShoppingReport:
    """A report with far more listings than the old hardcoded rank-label lists."""
    listings = [
        make_listing(store_name=f"Store {i}", regular=2.0 + i * 0.1, listing_id=f"L{i}",
                     verified=(i % 2 == 0))
        for i in range(n_listings)
    ]
    # Mix in another unit basis and an item with no data at all.
    listings.append(make_listing(store_name="Pint Place", regular=1.99, unit=UnitType.PINT, listing_id="LP"))
    return ShoppingReport(
        locale="Austin, TX",
        stores_searched=[],
        items_analyzed=[
            ItemRecommendation(
                item_query="cherry tomatoes",
                best_pick=listings[0],
                all_listings=listings,
                reasoning="cheapest verified",
            ),
            ItemRecommendation(
                item_query="unicorn fruit",
                best_pick=None,
                all_listings=[],
                reasoning="no data found",
            ),
        ],
        executive_summary="summary",
        total_potential_savings=0.0,
        data_quality_notes="some prices unverified",
    )


class TestRankLabel:
    def test_medals_for_top_three(self):
        assert "🥇" in rank_label(0)
        assert "🥉" in rank_label(2)

    def test_no_index_error_for_any_rank(self):
        for i in range(500):
            assert rank_label(i)
            assert rank_label(i, plain=True) == f"#{i + 1}"


class TestUnitDisplay:
    def test_no_more_per_slash_lb(self):
        assert unit_display("per_lb") == "/lb"
        assert unit_display("package") == "pkg"
        assert unit_display("unknown_unit") == "unknown_unit"


class TestGrouping:
    def test_grouped_listings_largest_group_first(self):
        rec = _big_report().items_analyzed[0]
        groups = grouped_listings(rec)
        assert groups[0][0] == "per_lb"
        assert groups[1][0] == "pint"
        # best-value first within each group
        per_lb = groups[0][1]
        assert per_lb[0].value_score <= per_lb[-1].value_score


class TestRenderers:
    def test_plain_renderer_handles_everything(self, capsys):
        render_report_plain(_big_report())
        out = capsys.readouterr().out
        assert "CHERRY TOMATOES" in out
        assert "#31" in out or "#30" in out       # deep ranks render without crashing
        assert "No price data found" in out        # honest no-data item
        assert "(unverified)" in out
        assert "Data quality:" in out

    def test_rich_renderer_does_not_crash(self):
        if not HAS_RICH:
            return
        render_report_rich(_big_report())  # 31 listings would IndexError in the old code
