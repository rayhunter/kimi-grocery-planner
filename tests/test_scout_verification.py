"""Tests for the fabrication guard: cross-checking scout prices against snippets."""
from agents.price_scout import PriceScoutDeps, PriceScoutResult, verify_scout_result
from tests.conftest import make_listing


def _deps(kroger, snippets):
    return PriceScoutDeps(
        store=kroger, item_query="cherry tomatoes", city="Austin, TX",
        captured_snippets=snippets,
    )


def _result(listings):
    return PriceScoutResult(
        listings=listings, confidence="high", data_freshness="today", raw_notes="",
    )


class TestVerifyScoutResult:
    def test_empty_listings_pass_through(self, kroger):
        result = verify_scout_result(_result([]), _deps(kroger, []))
        assert result.listings == []
        assert result.confidence == "high"

    def test_listings_without_any_snippets_are_discarded(self, kroger):
        result = verify_scout_result(
            _result([make_listing(regular=3.99)]),
            _deps(kroger, []),
        )
        assert result.listings == []
        assert result.confidence == "none"
        assert "Discarded" in result.raw_notes

    def test_price_found_in_snippets_is_verified(self, kroger):
        snippets = [{"title": "Cherry tomatoes $3.99/lb at Kroger", "snippet": "", "url": "http://x"}]
        listing = make_listing(regular=3.99, verified=False)
        result = verify_scout_result(_result([listing]), _deps(kroger, snippets))
        assert result.listings[0].price_verified is True

    def test_sale_price_match_also_verifies(self, kroger):
        snippets = [{"title": "", "snippet": "on sale for $2.49 this week", "url": ""}]
        listing = make_listing(regular=3.99, sale=2.49, verified=False)
        result = verify_scout_result(_result([listing]), _deps(kroger, snippets))
        assert result.listings[0].price_verified is True

    def test_fabricated_price_marked_unverified(self, kroger):
        snippets = [{"title": "tomatoes at Kroger", "snippet": "fresh produce daily", "url": ""}]
        listing = make_listing(regular=3.99, verified=True)
        result = verify_scout_result(_result([listing]), _deps(kroger, snippets))
        assert result.listings[0].price_verified is False
        assert "unverified" in result.raw_notes

    def test_store_name_and_query_normalized(self, kroger):
        snippets = [{"title": "$3.99", "snippet": "", "url": ""}]
        listing = make_listing(store_name="KROGER STORE #123", regular=3.99, item="tomatos")
        result = verify_scout_result(_result([listing]), _deps(kroger, snippets))
        assert result.listings[0].store_name == kroger.display_name
        assert result.listings[0].product_query == "cherry tomatoes"
