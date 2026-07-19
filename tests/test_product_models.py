"""Unit tests for price math, deal sanity checks, and unit-aware comparison."""
import pytest

from models.product import PricePoint, ProductListing, UnitType, DealType


class TestEffectivePrice:
    def test_regular_only(self):
        p = PricePoint(regular_price=4.99)
        assert p.effective_price == 4.99
        assert p.savings == 0.0

    def test_sale_price_wins(self):
        p = PricePoint(regular_price=4.99, sale_price=2.99)
        assert p.effective_price == 2.99
        assert p.savings == 2.00

    def test_percent_off_applies_without_sale_price(self):
        p = PricePoint(regular_price=5.00, percent_off=20)
        assert p.effective_price == 4.00
        assert p.savings == 1.00
        assert p.savings_pct == pytest.approx(20.0)

    def test_sale_price_takes_precedence_over_percent_off(self):
        p = PricePoint(regular_price=5.00, sale_price=3.00, percent_off=20)
        assert p.effective_price == 3.00


class TestDealSanityValidator:
    def test_bogus_sale_above_regular_is_dropped(self):
        p = PricePoint(regular_price=3.00, sale_price=4.00)
        assert p.sale_price is None
        assert p.effective_price == 3.00

    def test_sale_equal_to_regular_is_dropped(self):
        p = PricePoint(regular_price=3.00, sale_price=3.00)
        assert p.sale_price is None

    def test_implausible_percent_off_is_dropped(self):
        assert PricePoint(regular_price=3.00, percent_off=0).percent_off is None
        assert PricePoint(regular_price=3.00, percent_off=99).percent_off is None
        assert PricePoint(regular_price=3.00, percent_off=-5).percent_off is None
        assert PricePoint(regular_price=3.00, percent_off=50).percent_off == 50

    def test_zero_regular_price_rejected(self):
        with pytest.raises(ValueError):
            PricePoint(regular_price=0)


class TestUnitNormalization:
    def test_per_lb_passthrough(self):
        p = PricePoint(regular_price=3.00, unit=UnitType.PER_LB)
        assert p.price_per_lb == 3.00

    def test_per_oz_scales_to_lb(self):
        p = PricePoint(regular_price=0.25, unit=UnitType.PER_OZ)
        assert p.price_per_lb == pytest.approx(4.00)

    def test_per_kg_scales_to_lb(self):
        p = PricePoint(regular_price=2.20462, unit=UnitType.PER_KG)
        assert p.price_per_lb == pytest.approx(1.00, rel=1e-4)

    def test_count_units_not_normalizable(self):
        for unit in (UnitType.EACH, UnitType.BUNCH, UnitType.PACKAGE, UnitType.PINT):
            assert PricePoint(regular_price=3.00, unit=unit).price_per_lb is None


class TestComparisonGrouping:
    def _listing(self, unit, price=3.0, membership=None, verified=True):
        return ProductListing(
            store_name="Store",
            product_name="x",
            product_query="x",
            price_verified=verified,
            price=PricePoint(regular_price=price, unit=unit, membership_required=membership),
        )

    def test_weight_units_share_bucket(self):
        assert self._listing(UnitType.PER_LB).comparison_unit == "per_lb"
        assert self._listing(UnitType.PER_OZ).comparison_unit == "per_lb"
        assert self._listing(UnitType.PER_KG).comparison_unit == "per_lb"

    def test_count_units_have_own_buckets(self):
        assert self._listing(UnitType.PINT).comparison_unit == "pint"
        assert self._listing(UnitType.EACH).comparison_unit == "each"

    def test_comparable_price_normalizes_weight(self):
        oz = self._listing(UnitType.PER_OZ, price=0.25)
        assert oz.comparable_price == pytest.approx(4.00)

    def test_value_score_membership_penalty(self):
        free = self._listing(UnitType.EACH, price=3.00)
        gated = self._listing(UnitType.EACH, price=3.00, membership="Costco")
        assert gated.value_score > free.value_score

    def test_value_score_unverified_penalty(self):
        verified = self._listing(UnitType.EACH, price=3.00, verified=True)
        unverified = self._listing(UnitType.EACH, price=3.00, verified=False)
        assert unverified.value_score > verified.value_score

    def test_cross_weight_unit_ranking_is_fair(self):
        # $0.20/oz = $3.20/lb should rank WORSE than $2.99/lb.
        per_oz = self._listing(UnitType.PER_OZ, price=0.20)
        per_lb = self._listing(UnitType.PER_LB, price=2.99)
        assert per_lb.value_score < per_oz.value_score
