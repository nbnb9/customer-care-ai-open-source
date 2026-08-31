from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from .models.after_sales import (
    AfterSalesIdempotencyRecord,
    AfterSalesWorkflow,
    AfterSalesWorkflowEvent,
    MockOmsOperation,
)
from .models.audit import AuditEvent
from .models.conversations import (
    Conversation,
    ConversationEvent,
    ConversationFeedback,
    ConversationHandoff,
    ConversationIdempotencyRecord,
    ConversationMessage,
)
from .models.identity import Customer, Tenant, User, UserRole
from .models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeImport,
)
from .models.observability import ObservabilitySpan
from .models.qa import QAIdempotencyRecord, QAReviewIssue, QAReviewIssueEvent
from .models.self_service import R0SelfServiceFeatureFlag
from .models.tickets import (
    IdempotencyRecord,
    Ticket,
    TicketEvent,
    TicketPriority,
    TicketStatus,
)
from .models.triage import TriageSuggestion
from .shared.config import get_settings


def _parse(s: str | None):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _assert_seed_safe(db: Session, *, allow_destructive: bool) -> None:
    settings = get_settings()
    bind = db.get_bind()
    url = bind.engine.url if isinstance(bind, Connection) else bind.url
    database = url.database or ""
    host = url.host or ""

    if settings.app_env not in {"development", "test"}:
        raise RuntimeError("Refusing to seed outside development/test")
    if database in {"postgres", "template0", "template1"}:
        raise RuntimeError("Refusing to seed a PostgreSQL system database")
    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not is_loopback:
        raise RuntimeError("Refusing destructive seed against a non-local database")
    if database.endswith("_test"):
        return
    if not allow_destructive:
        raise RuntimeError(
            "Seed clears application tables; set ALLOW_DESTRUCTIVE_SEED=true "
            "for an explicit local-development run"
        )


def clear(db: Session, *, allow_destructive: bool = False) -> None:
    """按外键依赖的反序清空，让 seed 可反复重跑。"""
    _assert_seed_safe(db, allow_destructive=allow_destructive)
    for model in (
        ObservabilitySpan,
        MockOmsOperation,
        AfterSalesWorkflowEvent,
        AfterSalesIdempotencyRecord,
        AfterSalesWorkflow,
        QAReviewIssueEvent,
        QAIdempotencyRecord,
        QAReviewIssue,
        ConversationFeedback,
        ConversationEvent,
        ConversationHandoff,
        ConversationIdempotencyRecord,
        ConversationMessage,
        Conversation,
        R0SelfServiceFeatureFlag,
        KnowledgeChunk,
        KnowledgeDocument,
        KnowledgeImport,
        IdempotencyRecord,
        TriageSuggestion,
        TicketEvent,
        AuditEvent,
        Ticket,
        Customer,
        User,
        Tenant,
    ):
        db.execute(delete(model))
    db.flush()


def load_synthetic_world(db: Session) -> None:
    """Load the versioned synthetic fixture without clearing existing data."""

    import json
    from pathlib import Path

    # 从 seed.py 位置推导仓库根目录，避免依赖 CWD
    fixtures = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "world.json"
    world = json.loads(fixtures.read_text())

    for t in world["tenants"]:
        db.add(
            Tenant(
                id=t["id"],
                slug=t["slug"],
                name=t["name"],
                default_region=t["default_region"],
            )
        )
    db.flush()  # 先落 tenants，users/customers 的外键才能引用

    for u in world["users"]:
        db.add(
            User(
                id=u["id"],
                tenant_id=u["tenant_id"],
                role=UserRole(u["role"]),
                display_name=u["display_name"],
                email=u["email"],
                verification_level=u["verification_level"],
            )
        )
    db.flush()  # 先落 users，customers.user_id 才能引用

    for c in world["customers"]:
        db.add(
            Customer(
                id=c["id"],
                tenant_id=c["tenant_id"],
                user_id=c["user_id"],
                external_ref=c["external_ref"],
                region=c["region"],
            )
        )
    db.flush()  # 先落 customers，tickets.customer_id 才能引用

    for t in world["tickets"]:
        db.add(
            Ticket(
                id=t["id"],
                tenant_id=t["tenant_id"],
                customer_id=t["customer_id"],
                order_id=t.get("order_id"),
                status=TicketStatus(t["status"]),
                subject=t["subject"],
                description=t.get("description"),
                priority=TicketPriority(t["priority"]),
                assignee_id=t.get("assignee_id"),
                created_at=_parse(t.get("created_at")),
                closed_at=_parse(t.get("closed_at")),
            )
        )
    db.commit()


def seed(db: Session, *, allow_destructive: bool = False) -> None:
    clear(db, allow_destructive=allow_destructive)
    load_synthetic_world(db)


if __name__ == "__main__":
    from .db import SessionLocal

    db = SessionLocal()
    try:
        seed(db, allow_destructive=get_settings().allow_destructive_seed)
        print("seeded")
    finally:
        db.close()
