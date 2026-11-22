from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator = to_camel, populate_by_name = True, extra = "forbid")

class Out(BaseModel):
    model_config = ConfigDict(extra = "ignore")

class WalletOut(Out):
    depositedCents: int
    earningsCents: int
    totalCents: int
    frozen: bool
    frozenAt: str | None
    autoPayUntil: str | None = None

class WalletLineOut(Out):
    id: int
    kind: str
    bucket: str
    amountCents: int
    createdAt: str
    reason: str | None
    # Entry fees, refunds and guarantees: the event they were for.
    eventId: str | None = None
    eventName: str | None = None

class WalletHistoryOut(Out):
    items: list[WalletLineOut]
    nextBefore: int | None

class DepositIn(CamelModel):
    # What lands in the wallet, in cents; the 5% fee is added on top. Range checks are in the
    # service (with the messages users see); this only stops absurd numbers.
    amount_cents: int = Field(gt = 0, le = 100_000_000)
    # One per deposit dialog: retrying with the same key returns the same deposit.
    idempotency_key: str = Field(min_length = 16, max_length = 100, pattern = r"^[A-Za-z0-9-]+$")

class DepositStartOut(Out):
    depositId: str
    clientSecret: str
    amountCents: int
    feeCents: int
    totalCents: int
    status: str

class ManualMoveIn(CamelModel):
    email: str = Field(min_length = 3, max_length = 320)
    direction: str = Field(pattern = "^(credit|debit)$")
    amount_cents: int = Field(gt = 0, le = 100_000_000)
    reason: str = Field(min_length = 3, max_length = 500)
    reference: str = Field(min_length = 1, max_length = 200)
    # One per form submission: a double click or retry posts once.
    idempotency_key: str = Field(min_length = 16, max_length = 100, pattern = r"^[A-Za-z0-9-]+$")

class EventCancelIn(CamelModel):
    reason: str = Field(min_length = 3, max_length = 500)

class AutoPayIn(CamelModel):
    # 7, 30 or 90 days from now; 0 turns it off.
    days: int = Field(ge = 0, le = 90)

class DepositRefundIn(CamelModel):
    reason: str = Field(default = "", max_length = 500)

class AdminWalletOut(WalletOut):
    accountId: str
    email: str
    deposits: list[dict[str, Any]]
