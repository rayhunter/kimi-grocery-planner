"""
Test setup: a dummy API key must exist BEFORE any agent module is imported
(agents are constructed at import time), and real model requests are blocked
so no test can ever hit the Moonshot API.
"""
import os

os.environ.setdefault("MOONSHOT_API_KEY", "test-key-not-real")

# Importing any agent module runs config.load_dotenv(), which would pull the
# developer's REAL SERPAPI_KEY out of .env — and a real key makes web_search()
# route to SerpAPI and bill live requests. Set it empty rather than deleting it:
# load_dotenv() does not override variables already present, so an empty value
# both blocks the .env value and reads as falsy at every call site.
os.environ["SERPAPI_KEY"] = ""
# Belt-and-braces: no test may egress to a search backend at all.
os.environ["GROCERY_OFFLINE"] = "1"

import pydantic_ai.models

pydantic_ai.models.ALLOW_MODEL_REQUESTS = False

import pytest

from models.store import Store, StoreType, MembershipProgram
from models.product import ProductListing, PricePoint, DealType, UnitType


@pytest.fixture
def kroger() -> Store:
    return Store(
        name="Kroger",
        chain="Kroger",
        store_type=StoreType.CHAIN,
        address="123 Main St",
        city="Austin",
        state="TX",
        membership_programs=[
            MembershipProgram(name="Kroger Plus Card", annual_cost=0.0, discount_description="Loyalty prices")
        ],
        has_weekly_ad=True,
    )


@pytest.fixture
def target() -> Store:
    return Store(
        name="Super Target",
        chain="Target",
        store_type=StoreType.BIG_BOX,
        address="456 Oak Ave",
        city="Austin",
        state="TX",
        membership_programs=[
            MembershipProgram(name="Target Circle", annual_cost=0.0, discount_description="Free loyalty deals")
        ],
        has_weekly_ad=True,
    )


def make_listing(
    store_name: str = "Kroger",
    regular: float = 3.99,
    sale: float | None = None,
    unit: UnitType = UnitType.PER_LB,
    membership: str | None = None,
    verified: bool = True,
    listing_id: str | None = None,
    item: str = "cherry tomatoes",
) -> ProductListing:
    return ProductListing(
        store_name=store_name,
        product_name=f"{item} product",
        product_query=item,
        listing_id=listing_id,
        price_verified=verified,
        price=PricePoint(
            regular_price=regular,
            sale_price=sale,
            unit=unit,
            deal_type=DealType.WEEKLY_SALE if sale else DealType.REGULAR,
            membership_required=membership,
        ),
    )


@pytest.fixture
def listing_factory():
    return make_listing
