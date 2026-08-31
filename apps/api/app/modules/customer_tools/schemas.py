from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

OrderNumber = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderLookupRequest(StrictRequest):
    # The browser may only supply a candidate. Customer/tenant scope is never
    # accepted from the request body and is injected from CurrentPrincipal.
    order_number: OrderNumber


class OrderStatusRead(BaseModel):
    order_number: str
    status: str
    ordered_at: datetime


class WarrantyEligibilityRead(BaseModel):
    order_number: str
    eligible: bool
    coverage_ends_at: datetime | None
