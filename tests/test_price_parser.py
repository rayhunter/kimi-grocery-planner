"""Unit tests for the regex price/deal extraction used to verify scout output."""
from tools.price_parser import (
    extract_prices,
    detect_unit,
    detect_deal_type,
    detect_membership,
    parse_price_from_snippets,
)


class TestExtractPrices:
    def test_simple_dollar_amounts(self):
        assert extract_prices("Tomatoes $3.99, was $4.99") == [3.99, 4.99]

    def test_whole_dollar(self):
        assert 2.0 in extract_prices("only $2 today")

    def test_multibuy(self):
        prices = extract_prices("3 for $5.00 this week")
        assert 5.0 in prices

    def test_no_prices(self):
        assert extract_prices("fresh produce daily") == []


class TestDetectors:
    def test_detect_unit(self):
        assert detect_unit("$3.99 per lb") == "per_lb"
        assert detect_unit("sold by the pint") == "pint"
        assert detect_unit("$4 per bag") == "package"
        assert detect_unit("no unit mentioned") == "each"

    def test_detect_deal_type_percent(self):
        deal, pct = detect_deal_type("save 20% off this week only")
        assert deal == "percent_off"
        assert pct == 20.0

    def test_detect_deal_type_weekly(self):
        deal, pct = detect_deal_type("in this week's weekly ad")
        assert deal == "weekly_sale"
        assert pct is None

    def test_detect_membership(self):
        assert detect_membership("with your Target Circle account") == "Target Circle"
        assert detect_membership("Prime member deal") == "Amazon Prime"
        assert detect_membership("no membership here") is None


class TestParseFromSnippets:
    def test_parses_regular_and_sale(self):
        snippets = [
            {"title": "Cherry Tomatoes $4.99", "snippet": "on sale $2.99 per lb this week", "url": "http://x"},
        ]
        parsed = parse_price_from_snippets(snippets, "cherry tomatoes", "Kroger")
        assert parsed is not None
        assert parsed["regular_price"] == 4.99
        assert parsed["sale_price"] == 2.99
        assert parsed["unit"] == "per_lb"

    def test_filters_implausible_prices(self):
        snippets = [{"title": "Store 78701", "snippet": "call 512-555", "url": ""}]
        assert parse_price_from_snippets(snippets, "x", "y") is None

    def test_empty_snippets(self):
        assert parse_price_from_snippets([], "x", "y") is None
