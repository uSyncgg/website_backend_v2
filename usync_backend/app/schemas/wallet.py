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

class WalletLineOut(Out):
    id: int
    kind: str
    bucket: str
    amountCents: int
    createdAt: str
    reason: str | None

class WalletHistoryOut(Out):
    items: list[WalletLineOut]
    nextBefore: int | None

class DepositIn(CamelModel):
    # What lands in the wallet, in cents; the 5% fee is added on top. Range checks are in the
    # service (with the messages users see); this only stops absurd numbers.
    amount_cents: int = Field(gt = 0, le = 100_000_000)

class DepositStartOut(Out):
    depositId: str
    clientSecret: str
    amountCents: int
    feeCents: int
    totalCents: int

class ManualMoveIn(CamelModel):
    email: str = Field(min_length = 3, max_length = 320)
    direction: str = Field(pattern = "^(credit|debit)$")
    amount_cents: int = Field(gt = 0, le = 100_000_000)
    reason: str = Field(min_length = 3, max_length = 500)
    reference: str = Field(min_length = 1, max_length = 200)

class DepositRefundIn(CamelModel):
    reason: str = Field(default = "", max_length = 500)

class AdminWalletOut(WalletOut):
    accountId: str
    email: str
    deposits: list[dict[str, Any]]
