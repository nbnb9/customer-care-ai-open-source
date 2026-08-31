from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal

from . import service
from .schemas import (
    AfterSalesDraftCreate,
    AfterSalesWorkflowEventRead,
    AfterSalesWorkflowRead,
    AgentApprovalRequest,
    CustomerConfirmationRequest,
)

router = APIRouter(prefix="/v1/after-sales/workflows", tags=["after-sales-r2"])

IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=255),
]


def _serialize(
    result: object,
) -> AfterSalesWorkflowRead | dict[str, object]:
    if isinstance(result, dict):
        return result
    return service.serialize_workflow(result)  # type: ignore[arg-type]


@router.post("", response_model=AfterSalesWorkflowRead, status_code=201)
def create_after_sales_workflow(
    data: AfterSalesDraftCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: IdempotencyKey,
):
    return _serialize(service.create_workflow(db, principal, data, idempotency_key))


@router.get("", response_model=list[AfterSalesWorkflowRead])
def list_after_sales_workflows(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    ticket_id: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
):
    return [
        service.serialize_workflow(workflow)
        for workflow in service.list_workflows(db, principal, ticket_id=ticket_id)
    ]


@router.get("/{workflow_id}", response_model=AfterSalesWorkflowRead)
def get_after_sales_workflow(
    workflow_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.serialize_workflow(service.get_workflow(db, principal, workflow_id))


@router.get("/{workflow_id}/events", response_model=list[AfterSalesWorkflowEventRead])
def list_after_sales_workflow_events(
    workflow_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return [
        service.serialize_event(event)
        for event in service.list_workflow_events(db, principal, workflow_id)
    ]


@router.post(
    "/{workflow_id}/customer-confirmation", response_model=AfterSalesWorkflowRead
)
def confirm_after_sales_workflow(
    workflow_id: str,
    data: CustomerConfirmationRequest,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: IdempotencyKey,
):
    return _serialize(
        service.confirm_by_customer(db, principal, workflow_id, data, idempotency_key)
    )


@router.post("/{workflow_id}/agent-approval", response_model=AfterSalesWorkflowRead)
def approve_after_sales_workflow(
    workflow_id: str,
    data: AgentApprovalRequest,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: IdempotencyKey,
):
    return _serialize(
        service.approve_by_agent(db, principal, workflow_id, data, idempotency_key)
    )
