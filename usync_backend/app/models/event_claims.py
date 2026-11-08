import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, text

# Allowed values, shared with the request schema so the two can't drift apart.
CLAIM_STATUSES = ("pending", "approved", "rejected")
CLAIM_ROLES = ("owner", "organizer", "staff", "other")
CONTACT_METHODS = ("email", "discord", "twitter")

def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"

class EventClaims(Base):
    """
    SQL Alchemy model describing the event_claims table: a host account asking to be recorded as
    the owner of an existing listing. uSync reviews the proof by hand; approving a claim sets
    events.user_id to the claimant's account.
    """

    __tablename__ = "event_claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete = "CASCADE"), index = True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"), index = True)
    role: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column()
    contact_method: Mapped[str] = mapped_column()
    contact: Mapped[str] = mapped_column()
    # Path in the private event_claim_proofs bucket (<account_id>/<claim_id>.webp), never a public URL.
    proof_path: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default = "pending", server_default = text("'pending'"))
    review_note: Mapped[str | None] = mapped_column(nullable = True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint(_in("status", CLAIM_STATUSES), name = "ck_event_claims_status"),
        CheckConstraint(_in("role", CLAIM_ROLES), name = "ck_event_claims_role"),
        CheckConstraint(_in("contact_method", CONTACT_METHODS), name = "ck_event_claims_contact_method"),
        # One open claim per account per event. Approved/rejected ones don't count, so a rejected
        # host can try again with better proof.
        Index(
            "uq_event_claims_pending",
            "event_id", "account_id",
            unique = True,
            postgresql_where = text("status = 'pending'")
        ),
    )
