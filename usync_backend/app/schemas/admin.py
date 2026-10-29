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
