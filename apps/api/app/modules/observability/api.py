"""Supervisor-only APIs for the local Step 39 trace viewer."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal

from . import service
from .schemas import ObservabilityOverviewRead, ObservabilityTraceRead

router = APIRouter(prefix="/v1/admin/observability", tags=["observability"])


@router.get("/overview", response_model=ObservabilityOverviewRead)
def get_observability_overview(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.get_overview(db, principal)


@router.get(
    "/traces/by-request/{request_id}", response_model=list[ObservabilityTraceRead]
)
def get_traces_by_request(
    request_id: Annotated[str, Path(min_length=1, max_length=128)],
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.traces_for_request(db, principal, request_id)


@router.get(
    "/traces/conversations/{conversation_id}",
    response_model=list[ObservabilityTraceRead],
)
def get_traces_by_conversation(
    conversation_id: Annotated[str, Path(min_length=1, max_length=128)],
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.traces_for_conversation(db, principal, conversation_id)


@router.get(
    "/traces/workflows/{workflow_id}",
    response_model=list[ObservabilityTraceRead],
)
def get_traces_by_workflow(
    workflow_id: Annotated[str, Path(min_length=1, max_length=128)],
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.traces_for_workflow(db, principal, workflow_id)
