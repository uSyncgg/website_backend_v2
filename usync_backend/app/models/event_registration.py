import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, text, func, UniqueConstraint, Index, DateTime, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB

class PassPools(Base):
    """
    SQL Alchemy model describing the pass_pools table.

    A pool is a shared sales limit across several pass tiers of the same event
    (e.g. regular + VIP team passes share 15 slots). Tiers with no pool only
    answer to their own capacity.
    """

    __tablename__ = "pass_pools"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="ck_pass_pools_capacity_positive"),
        CheckConstraint("sold_count >= 0", name="ck_pass_pools_sold_count_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column()
    capacity: Mapped[int] = mapped_column()
    sold_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

class EventPassTiers(Base):
    """
    SQL Alechemy model describing the event_pass_tiers table.
    """

    __tablename__ = "event_pass_tiers"
    __table_args__ = (
        UniqueConstraint("event_id", "tier_name", name = "uq_event_pass_tiers_event_tier_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None)
    host_email: Mapped[str | None] = mapped_column(nullable = True, default = "contact@usync.gg", server_default = text("'contact@usync.gg'"))
    tier_name: Mapped[str] = mapped_column()          
    price_cents: Mapped[int] = mapped_column()
    capacity: Mapped[int | None] = mapped_column(nullable=True, default=None)  # this tier's own cap
    sold_count: Mapped[int] = mapped_column(default = 0, server_default=text("0"))
    pool_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pass_pools.id"), index=True, nullable=True, default=None)
    pool: Mapped[PassPools | None] = relationship(lazy="raise")
    requires_team: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    form_fields: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    player_fields: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    team_size: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    @property
    def pool_capacity(self) -> int | None:
        return self.pool.capacity if self.pool else None

    @property
    def pool_sold_count(self) -> int | None:
        return self.pool.sold_count if self.pool else None

class EventRegistrations(Base):
    """
    SQL Alchemy model describing the event_registrations table.
    """

    __tablename__ = "event_registrations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, unique=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    pass_tier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_pass_tiers.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, default=None)
    team_name: Mapped[str | None] = mapped_column(nullable=True, default=None)              
    team_name_normalized: Mapped[str | None] = mapped_column(nullable=True, default=None)   
    contact_username: Mapped[str | None] = mapped_column()     
    contact_email: Mapped[str] = mapped_column()
    org_twitter: Mapped[str | None] = mapped_column(nullable=True, default = None)
    player_info: Mapped[list[dict]] = mapped_column(JSONB, default=list)          
    price_snapshot_cents: Mapped[int] = mapped_column()  
    fee_snapshot_cents: Mapped[int] = mapped_column()     
    total_snapshot_cents: Mapped[int] = mapped_column()   
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(unique=True, nullable=True, default=None)
    payment_status: Mapped[str] = mapped_column(default="pending", server_default=text("'pending'"))  
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable=True, default=None)
    receipt_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable=True, default=None)
    host_confirmation_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True, default = None)
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index(
            "uq_registrations_event_team_name", "event_id", "team_name_normalized",
            unique=True, postgresql_where=text("team_name_normalized IS NOT NULL AND payment_status NOT IN ('expired', 'canceled', 'refunded')"),
        ),
        Index(
            "uq_registrations_individual_contact", "event_id", "pass_tier_id", "contact_email",
            unique=True, postgresql_where=text("team_name_normalized IS NULL AND payment_status NOT IN ('expired', 'canceled', 'refunded')"),
        ),
    )
