from __future__ import annotations
from pydantic import BaseModel, Field
from enum import Enum


class StoreType(str, Enum):
    CHAIN = "chain"
    LOCAL_MARKET = "local_market"
    BIG_BOX = "big_box"
    SPECIALTY = "specialty"
    WHOLESALE = "wholesale"


class MembershipProgram(BaseModel):
    name: str = Field(description="e.g. 'Amazon Prime', 'Target Circle', 'Costco Gold Star'")
    annual_cost: float | None = Field(default=None, description="Annual membership fee in USD")
    discount_description: str = Field(description="What discounts/benefits are provided")


class Store(BaseModel):
    name: str
    chain: str | None = Field(default=None, description="Parent chain name if applicable")
    store_type: StoreType
    address: str
    city: str
    state: str
    zip_code: str | None = None
    phone: str | None = None
    hours: str | None = None
    website: str | None = None
    membership_programs: list[MembershipProgram] = Field(default_factory=list)
    has_weekly_ad: bool = Field(default=False)
    weekly_ad_url: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.chain})" if self.chain and self.chain != self.name else self.name
