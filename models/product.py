from __future__ import annotations
from pydantic import BaseModel, Field, model_validator
from enum import Enum
from datetime import date


class DealType(str, Enum):
    REGULAR = "regular"
    WEEKLY_SALE = "weekly_sale"
    MEMBERSHIP = "membership"
    PERCENT_OFF = "percent_off"
    BUY_X_GET_Y = "buy_x_get_y"
    DIGITAL_COUPON = "digital_coupon"
    CLEARANCE = "clearance"
    LOYALTY = "loyalty"


class UnitType(str, Enum):
    EACH = "each"
    PER_LB = "per_lb"
    PER_OZ = "per_oz"
    PER_KG = "per_kg"
    BUNCH = "bunch"
    PACKAGE = "package"
    PINT = "pint"


# Weight units can be normalized to a common per-lb basis; count/volume units
# (each, bunch, package, pint) are only comparable to the same unit.
_LB_CONVERSION = {
    UnitType.PER_LB: 1.0,
    UnitType.PER_OZ: 16.0,
    UnitType.PER_KG: 1.0 / 2.20462,
}


class PricePoint(BaseModel):
    regular_price: float = Field(gt=0, description="Normal retail price")
    sale_price: float | None = Field(default=None, description="Current sale price if on deal")
    unit: UnitType = Field(default=UnitType.EACH)
    unit_description: str | None = Field(default=None, description="e.g. '12 oz package', '1 lb bunch'")
    deal_type: DealType = Field(default=DealType.REGULAR)
    deal_description: str | None = Field(default=None, description="e.g. '20% off with Target Circle'")
    membership_required: str | None = Field(default=None, description="Name of membership required for deal")
    percent_off: float | None = Field(default=None, description="Percentage off regular price")
    valid_from: date | None = None
    valid_until: date | None = None
    source_url: str | None = Field(default=None, description="URL where price was found")

    @model_validator(mode="after")
    def _sanity_check_deal(self) -> PricePoint:
        # A "sale" that isn't below the regular price is not a deal.
        if self.sale_price is not None and self.sale_price >= self.regular_price:
            self.sale_price = None
        # Percent-off outside a plausible range is noise, not a promotion.
        if self.percent_off is not None and not (0 < self.percent_off < 95):
            self.percent_off = None
        return self

    @property
    def effective_price(self) -> float:
        """Best available price: explicit sale price, else percent-off applied, else regular."""
        if self.sale_price is not None:
            return self.sale_price
        if self.percent_off:
            return round(self.regular_price * (1 - self.percent_off / 100), 2)
        return self.regular_price

    @property
    def savings(self) -> float:
        """Dollar savings off regular price."""
        return self.regular_price - self.effective_price

    @property
    def savings_pct(self) -> float:
        """Percentage savings off regular price."""
        if self.regular_price == 0:
            return 0.0
        return (self.savings / self.regular_price) * 100

    @property
    def price_per_lb(self) -> float | None:
        """Effective price normalized to per-lb, or None for non-weight units."""
        factor = _LB_CONVERSION.get(self.unit)
        if factor is None:
            return None
        return self.effective_price * factor


class ProductListing(BaseModel):
    store_name: str
    product_name: str = Field(description="Normalized product name")
    product_query: str = Field(description="Original user search term")
    price: PricePoint
    in_stock: bool = Field(default=True)
    notes: str | None = None
    listing_id: str | None = Field(
        default=None,
        description="Stable ID assigned by the orchestrator; used by the analyst to reference listings",
    )
    price_verified: bool = Field(
        default=False,
        description="True when the quoted price also appears in the raw search snippets",
    )

    @property
    def comparison_unit(self) -> str:
        """Bucket for fair comparison: weight units share one bucket, others compare per-unit."""
        if self.price.unit in _LB_CONVERSION:
            return "per_lb"
        return self.price.unit.value

    @property
    def comparable_price(self) -> float:
        """Price on the comparison_unit basis (per-lb for weight units)."""
        return self.price.price_per_lb if self.price.unit in _LB_CONVERSION else self.price.effective_price

    @property
    def value_score(self) -> float:
        """
        Sort key: lower is better. Only meaningful between listings that share
        the same comparison_unit — cross-unit comparison is apples-to-oranges,
        so callers must group by comparison_unit before ranking.
        Penalizes membership-gated prices slightly (upfront cost barrier) and
        unverified prices (may be model-estimated rather than found in sources).
        """
        base = self.comparable_price
        if self.price.membership_required:
            base *= 1.05
        if not self.price_verified:
            base *= 1.02
        return base
