from pydantic import BaseModel

from app.models.identity import UserRole


class CurrentPrincipal(BaseModel):
    user_id: str
    tenant_id: str
    role: UserRole
    verification_level: str
    customer_id: str | None = None  # 仅 customer 角色有值
