import logging
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .db import get_db
from .modules.after_sales.api import router as after_sales_router
from .modules.conversations.api import handoffs_router
from .modules.conversations.api import router as conversations_router
from .modules.customer_tools.api import router as customer_tools_router
from .modules.identity.deps import get_current_principal
from .modules.identity.principal import CurrentPrincipal
from .modules.knowledge.api import router as knowledge_router
from .modules.knowledge.retrieval_api import router as knowledge_retrieval_router
from .modules.observability.api import router as observability_router
from .modules.qa.api import router as qa_router
from .modules.self_service.api import router as self_service_router
from .modules.tickets.api import router as tickets_router
from .modules.triage.api import router as triage_router
from .shared.config import get_settings
from .shared.errors import register_error_handlers
from .shared.logging import setup_logging
from .shared.middleware import RequestBodyLimitMiddleware, RequestIDMiddleware

setup_logging()
logger = logging.getLogger("app")

settings = get_settings()

app = FastAPI(
    title="CustomerCare AI API",
    version="0.1.0",
    description="CustomerCare AI 服务台后端",
)

app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=lambda: settings.max_request_body_bytes,
)
app.add_middleware(RequestIDMiddleware)
register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Idempotency-Key",
        "Last-Event-ID",
        "X-Demo-User-ID",
        "X-Request-ID",
        "traceparent",
    ],
    expose_headers=["X-Request-ID", "X-Trace-ID", "traceparent"],
)


@app.get("/health")
async def health_check():
    logger.info("health check ok", extra={"event": "health_check"})
    return {"status": "ok", "service": "customer-care-api"}


@app.get("/ready")
def readiness_check(db: Annotated[Session, Depends(get_db)]):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("readiness database check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "service": "customer-care-api"},
        )
    return {"status": "ready", "service": "customer-care-api"}


# 步骤 17 演示端点：展示服务端解析出的当前身份（后续端点都这样用 Depends）
@app.get("/whoami")
async def whoami(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
):
    return principal.model_dump()


app.include_router(tickets_router)
app.include_router(triage_router)
app.include_router(knowledge_router)
app.include_router(knowledge_retrieval_router)
app.include_router(conversations_router)
app.include_router(handoffs_router)
app.include_router(self_service_router)
app.include_router(customer_tools_router)
app.include_router(after_sales_router)
app.include_router(qa_router)
app.include_router(observability_router)
