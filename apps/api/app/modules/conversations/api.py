from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.modules.identity.deps import get_current_principal
from app.modules.identity.principal import CurrentPrincipal
from app.shared.observability import current_traceparent

from . import service
from .schemas import (
    ConversationCreate,
    ConversationFeedbackCreate,
    ConversationFeedbackRead,
    ConversationHandoffCreate,
    ConversationHandoffRead,
    ConversationMessageAccepted,
    ConversationMessageCreate,
    ConversationRead,
    HandoffClaimCreate,
    HandoffDetailRead,
    HandoffQueueItemRead,
    HandoffResolutionCreate,
)

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
handoffs_router = APIRouter(prefix="/v1/handoffs", tags=["handoffs"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    data: ConversationCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
):
    return service.create_conversation(db, principal, data, idempotency_key)


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationMessageAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_message(
    conversation_id: str,
    data: ConversationMessageCreate,
    background_tasks: BackgroundTasks,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
):
    submission = service.create_message(
        db, principal, conversation_id, data, idempotency_key
    )
    if submission.should_generate_reply:
        background_tasks.add_task(
            service.generate_controlled_reply,
            db.get_bind(),
            submission.response.conversation_id,
            submission.response.message_id,
            submission.response.request_id,
            current_traceparent(),
        )
    return submission.response


@router.get("/{conversation_id}/events")
async def get_conversation_events(
    conversation_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    after_sequence: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    follow: Annotated[bool, Query()] = True,
):
    resume_after = service.resolve_after_sequence(
        after_sequence=after_sequence, last_event_id=last_event_id
    )
    service.assert_can_stream(db, principal, conversation_id)
    return StreamingResponse(
        service.stream_events(
            db.get_bind(),
            conversation_id=conversation_id,
            after_sequence=resume_after,
            follow=follow,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{conversation_id}/handoffs",
    response_model=ConversationHandoffRead,
    status_code=status.HTTP_201_CREATED,
)
def create_handoff(
    conversation_id: str,
    data: ConversationHandoffCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
):
    return service.create_handoff(db, principal, conversation_id, data, idempotency_key)


@router.post(
    "/{conversation_id}/feedback",
    response_model=ConversationFeedbackRead,
    status_code=status.HTTP_201_CREATED,
)
def create_feedback(
    conversation_id: str,
    data: ConversationFeedbackCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
):
    return service.create_feedback(
        db, principal, conversation_id, data, idempotency_key
    )


@handoffs_router.get("", response_model=list[HandoffQueueItemRead])
def list_handoffs(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.list_handoffs(db, principal)


@handoffs_router.get("/{handoff_id}", response_model=HandoffDetailRead)
def get_handoff(
    handoff_id: str,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
):
    return service.get_handoff(db, principal, handoff_id)


@handoffs_router.post("/{handoff_id}/claim", response_model=HandoffDetailRead)
def claim_handoff(
    handoff_id: str,
    data: HandoffClaimCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
):
    return service.claim_handoff(db, principal, handoff_id, data, idempotency_key)


@handoffs_router.post("/{handoff_id}/resolution", response_model=HandoffDetailRead)
def resolve_handoff(
    handoff_id: str,
    data: HandoffResolutionCreate,
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=255),
    ],
):
    return service.resolve_handoff(db, principal, handoff_id, data, idempotency_key)
