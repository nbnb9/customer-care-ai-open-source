from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class R0SelfServiceFeatureFlag(Base):
    """Tenant-local canary and kill switch for customer R0 FAQ automation."""

    __tablename__ = "r0_self_service_feature_flags"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # An empty list does not enable a tenant-wide rollout. ``allow_all_customers``
    # must be set explicitly by the tenant supervisor.
    allow_all_customers: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    customer_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
