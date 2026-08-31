from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal

from . import service
from .schemas import (
    OrderLookupRequest,
    OrderStatusRead,
    WarrantyEligibilityRead,
)

router = APIRouter(prefix="/v1/customer-tools", tags=["customer-read-tools"])


@router.post("/order-status", response_model=OrderStatusRead)
def read_order_status(
    data: OrderLookupRequest,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.query_order_status(db, principal, data)


@router.post("/warranty-eligibility", response_model=WarrantyEligibilityRead)
def read_warranty_eligibility(
    data: OrderLookupRequest,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.query_warranty_eligibility(db, principal, data)
