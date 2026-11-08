import uuid

from datetime import datetime
from typing import Any
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, text
from sqlalchemy.dialects.postgresql import JSONB

class Admins(Base):
    """
    SQL Alchemy model describing the admins table: one row per account allowed into the admin
    dashboard. Rows are only added or removed by hand in the Supabase SQL editor.
    """

    __tablename__ = "admins"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"), primary_key = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

class AdminAuditLog(Base):
    """
    SQL Alchemy model describing the admin_audit_log table: one row per allowed admin read or
    action. admin_email is a copy so a row still says who acted after their account is deleted
    (admin_id becomes NULL then).
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always = True), primary_key = True)
    admin_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True)
    admin_email: Mapped[str] = mapped_column()
    action: Mapped[str] = mapped_column()
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
