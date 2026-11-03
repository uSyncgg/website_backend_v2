from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel

class RoleTotals(BaseModel):
    total: int
    players: int
    hosts: int
    venues: int

class SignupDay(RoleTotals):
    date: date

class GameInterest(BaseModel):
    players: dict[str, int]
    hosts: dict[str, int]

class AdminAnalyticsOut(BaseModel):
    generated_at: datetime
    totals: RoleTotals
    signups: list[SignupDay]
    games: GameInterest

class AdminEvent(BaseModel):
    id: UUID
    name: str
    event_type: str
    game: str
    path: str | None
    status: str
    verified: bool
    host_owned: bool
    updated_at: datetime
    # Nothing records who made an edit yet; always None until an updated_by column exists.
    updated_by: str | None = None

class AdminEventsOut(BaseModel):
    generated_at: datetime
    events: list[AdminEvent]

class AdminAccount(BaseModel):
    id: UUID
    # None until the account finishes sign-up and gets a users row.
    username: str | None
    email: str | None
    created_at: datetime
    is_player: bool
    is_host: bool
    is_venue: bool
    profile_complete: bool
    games: list[str]
    # Platform key -> handle, only for platforms the account has linked.
    linked: dict[str, str]

class AdminAccountsOut(BaseModel):
    generated_at: datetime
    accounts: list[AdminAccount]
