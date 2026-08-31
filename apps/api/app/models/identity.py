from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class UserRole(StrEnum):
    customer = "customer"
    agent = "agent"
    supervisor = "supervisor"
    knowledge_operator = "knowledge_operator"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    default_region: Mapped[str] = mapped_column(String)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    display_name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    verification_level: Mapped[str] = mapped_column(String)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    external_ref: Mapped[str] = mapped_column(String)
    region: Mapped[str] = mapped_column(String)
