from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal

from . import service
from .schemas import R0SelfServiceFeatureFlagRead, R0SelfServiceFeatureFlagUpdate

router = APIRouter(prefix="/v1/admin/self-service", tags=["self-service"])


@router.get("/r0-feature-flag", response_model=R0SelfServiceFeatureFlagRead)
def get_r0_feature_flag(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.read_r0_feature_flag(db, principal)


@router.put("/r0-feature-flag", response_model=R0SelfServiceFeatureFlagRead)
def put_r0_feature_flag(
    data: R0SelfServiceFeatureFlagUpdate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.update_r0_feature_flag(db, principal, data)
