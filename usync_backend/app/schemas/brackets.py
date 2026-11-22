import uuid

from datetime import datetime
from typing import Annotated, Any, Literal
from pydantic import StringConstraints, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

# Request and response bodies for /brackets. The frontend speaks camelCase (the shape the
# bracket pages use), so fields take camelCase aliases and also accept snake_case. Response
# models list every field the API may send; anything not listed here never leaves the API.

class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator = to_camel, populate_by_name = True, extra = "ignore")

class Out(BaseModel):
    """
    Response models: built from the services' camelCase dicts, sent as they are.
    """

    model_config = ConfigDict(extra = "ignore")

# ---- requests ------------------------------------------------------------------

class SignupQuestion(CamelModel):
    label: str = Field(min_length = 1, max_length = 120)
    type: str = Field(default = "text", pattern = "^(text|select)$")
    options: str = Field(default = "", max_length = 1000)
    required: bool = False

class SignupForm(CamelModel):
    player_fields: list[str] = Field(default_factory = list, max_length = 10)
    questions: list[SignupQuestion] = Field(default_factory = list, max_length = 20)

Restriction = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 200)]

class MoneySettings(CamelModel):
    """
    An event's entry fee and prize (GLOSSARY.md). All optional: leaving them out is a free event.
    The service checks the combination (app.services.event_money.money_settings).
    """

    # Per player, in cents; 0 or null = free entry.
    entry_fee_cents: int | None = Field(default = None, ge = 0, le = 50_000)
    prize_funding: Literal["guaranteed", "entry_funded"] | None = None
    # Guaranteed events: whole dollars, in cents.
    guarantee_cents: int | None = Field(default = None, gt = 0, le = 10_000_000)
    # Entry-funded events: the host's cut of the pool after the platform fee, in basis points.
    host_cut_bps: int | None = Field(default = None, ge = 0, le = 5_000)
    # The prize pool's split by placement, in basis points (100 = 1%), adding up to 10000.
    payout_structure: list[int] | None = Field(default = None, min_length = 1, max_length = 16)

class EventCreate(MoneySettings):
    host_profile: str
    name: str = Field(min_length = 1, max_length = 80)
    event_type: str = Field(pattern = "^(league|lan|tournament)$")
    game: str = Field(min_length = 1, max_length = 60, description = "A game key from /brackets/meta")
    game_title: str | None = Field(default = None, max_length = 80, description = "A title key, for games that have titles")
    game_mode: str | None = Field(default = None, max_length = 80, description = "A mode key, for games that have modes")
    format: str = Field(min_length = 1, max_length = 40)
    team_size: str = Field(pattern = "^([1-9]|10)v([1-9]|10)$")
    best_of: int = Field(ge = 1, le = 9)
    final_best_of: int | None = Field(default = None, ge = 1, le = 9)
    max_entrants: int | None = Field(default = None, ge = 2, le = 1024)
    min_entrants: int | None = Field(default = None, ge = 2, le = 1024)
    platform: str = Field(default = "pc_allowed", pattern = "^(pc_allowed|console_only)$")
    restrictions: list[Restriction] = Field(default_factory = list, max_length = 10)
    admins: list[str] = Field(default_factory = list, max_length = 10)
    starts_at: datetime
    venue: str = Field(default = "", max_length = 200)
    entry_fee: str = Field(default = "Free", max_length = 40)
    prize: str = Field(default = "", max_length = 200)
    description: str = Field(default = "", max_length = 5000)
    rules: str = Field(default = "", max_length = 5000)
    registration_open: bool = True
    no_show_minutes: int | None = Field(default = None, ge = 0, le = 240)
    payments: dict = Field(default_factory = dict)
    signup_form: SignupForm = Field(default_factory = SignupForm)

class EventUpdate(MoneySettings):
    name: str | None = Field(default = None, min_length = 1, max_length = 80)
    description: str | None = Field(default = None, max_length = 5000)
    rules: str | None = Field(default = None, max_length = 5000)
    prize: str | None = Field(default = None, max_length = 200)
    venue: str | None = Field(default = None, max_length = 200)
    best_of: int | None = Field(default = None, ge = 1, le = 9)
    final_best_of: int | None = Field(default = None, ge = 1, le = 9)
    max_entrants: int | None = Field(default = None, ge = 2, le = 1024)
    min_entrants: int | None = Field(default = None, ge = 2, le = 1024)
    platform: str | None = Field(default = None, pattern = "^(pc_allowed|console_only)$")
    restrictions: list[Restriction] | None = Field(default = None, max_length = 10)
    registration_open: bool | None = None
    no_show_minutes: int | None = Field(default = None, ge = 0, le = 240)

class EventDelay(CamelModel):
    starts_at: datetime
    reason: str = Field(default = "", max_length = 300)

class Registration(CamelModel):
    # Every entry comes from a team (its public id), entered by its owner or a captain.
    team_id: str | None = None
    # Exactly the event's team size in players from the team (profile public ids); in a solo event
    # the one player who'll compete, the captain or a teammate.
    players: list[str] = Field(default_factory = list, max_length = 10)
    email: str = Field(max_length = 200)
    answers: dict[str, str] = Field(default_factory = dict)
    pass_name: str | None = Field(default = None, alias = "pass", max_length = 80)
    # Events with an entry fee: 'team' (the captain pays for everyone) or 'each' (every player
    # pays their own; needs Auto-Pay on for everyone but the captain).
    pay_mode: Literal["team", "each"] | None = None

    @field_validator("email")
    @classmethod
    def email_shape(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1] or " " in v:
            raise ValueError("Enter a valid email.")
        return v

    @field_validator("answers")
    @classmethod
    def answer_lengths(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 30 or any(len(k) > 120 or len(x) > 1000 for k, x in v.items()):
            raise ValueError("Answers are too long.")
        return {k: x.strip() for k, x in v.items()}

class AutoPayRequest(CamelModel):
    # The team asking, and the teammates (profile public ids) to ask to turn Auto-Pay on.
    team_id: str = Field(min_length = 1, max_length = 64)
    players: list[str] = Field(min_length = 1, max_length = 10)

class PlayersUpdate(CamelModel):
    players: list[str] = Field(max_length = 10)
    # Which entry, when the account acts for several (a team's entries in a solo event).
    entrant_id: str | None = None

class SeedsUpdate(CamelModel):
    seeds: list[str] = Field(max_length = 1024)

class MatchScore(CamelModel):
    score_a: int = Field(ge = 0, le = 9)
    score_b: int = Field(ge = 0, le = 9)

class Ruling(CamelModel):
    action: Literal["award", "clear", "uphold"]
    score_a: int | None = Field(default = None, ge = 0, le = 9)
    score_b: int | None = Field(default = None, ge = 0, le = 9)
    forfeit: bool = False

class NotificationsRead(BaseModel):
    ids: list[uuid.UUID] | None = None

class AdminInvite(CamelModel):
    profile: str = Field(min_length = 1, max_length = 64)

class AdminResponse(CamelModel):
    accept: bool

# ---- responses -----------------------------------------------------------------

class ProfileRef(Out):
    type: str | None = None
    username: str
    public_id: str
    profile_picture: str | None = None

class EntrantOut(Out):
    id: str
    name: str
    captain: str
    status: str
    registeredAt: str | None
    teamId: str | None
    teamName: str | None = None
    players: list[ProfileRef]
    roster: list[str]
    rosterSize: int
    payMode: str | None = None
    paidPlayers: list[str] = []
    # Only for the host, admins and the entrant's own captain / player.
    email: str | None = None
    answers: dict[str, str] | None = None
    pass_: str | None = Field(default = None, alias = "pass", serialization_alias = "pass")

    model_config = ConfigDict(extra = "ignore", populate_by_name = True)

class AdminOut(Out):
    id: str
    status: str
    username: str
    public_id: str
    profile_picture: str | None = None

class BracketOut(Out):
    status: str
    format: str
    seeds: list[str]
    # The engine's bracket ({rounds, losersRounds?, grandFinal?, standings?, champion?,
    # placements, progress}) with each match's details; drawn by the frontend as it is.
    built: dict[str, Any]
    # Per playable match: the viewer's view of it (state, reports, timers, what they can do).
    matches: dict[str, dict[str, Any]]

class EventOut(Out):
    id: str
    name: str
    eventType: str
    game: str
    gameKey: str
    gameTitle: str
    gameTitleKey: str | None
    gameMode: str | None
    gameModeKey: str | None
    format: str
    formatLabel: str
    teamSize: str
    bestOf: int
    finalBestOf: int | None
    maxEntrants: int | None
    minEntrants: int | None
    platform: str
    restrictions: list[str]
    startsAt: str
    locationType: str
    venue: str
    entryFee: str
    prize: str
    description: str
    rules: str
    registrationOpen: bool
    noShowMinutes: int | None
    payments: dict[str, Any]
    signupForm: dict[str, Any]
    delays: list[dict[str, Any]]
    createdAt: str | None
    stage: str
    # Null once the host's profile is deleted (only finished or cancelled events outlive it).
    hostProfile: str | None
    host: ProfileRef | None
    # entryFeeCents, prizeFunding, guaranteeCents, guaranteeFunded, hostCutBps, payoutStructure,
    # platformFeeBps, poolCents, cancelledAt, cancelReason, payoutDueAt, paidOutAt.
    money: dict[str, Any]
    # Final placements once the bracket is complete, best first: [{entrantId, placement,
    # prizeCents}]. Entrants can share a placement; prizeCents is set once the event pays out.
    standings: list[dict[str, Any]] = []
    entrants: list[EntrantOut]
    canManage: bool
    isHost: bool
    admins: list[AdminOut]
    openIssues: int
    viewerEntrantId: str | None
    viewerCanAct: bool
    viewerActsFor: list[str] = []
    bracket: BracketOut | None

class EventSummary(Out):
    id: str
    name: str
    eventType: str
    game: str
    gameTitle: str
    format: str
    formatLabel: str
    teamSize: str
    startsAt: str
    stage: str
    hostProfile: str | None
    maxEntrants: int | None
    entryFeeCents: int | None = None
    prizeFunding: str | None = None
    guaranteeCents: int | None = None
    signedUp: int
    needsPlayers: int
    pending: int
    canManage: bool
    isHost: bool
    openIssues: int
    viewerEntrantId: str | None
    viewerEntrantName: str | None
    viewerMatch: dict[str, Any] | None
    winner: str | None
    viewerPlace: int | None
    viewerPrizeCents: int | None = None

class EventPage(Out):
    events: list[EventSummary]
    nextCursor: str | None

class HostedEventCard(Out):
    """
    A Hosted Event as the public game pages list it. Public on purpose: nothing host-only
    (open issues, admin flags, the viewer's entry) and nothing the event page wouldn't show.
    """

    id: str
    name: str
    eventType: str
    hostProfile: str | None
    game: str
    gameTitle: str
    gameMode: str
    format: str
    formatLabel: str
    teamSize: str
    startsAt: str
    platform: str
    locationType: str
    venue: str
    entryFeeCents: int | None
    prizeFunding: str | None
    guaranteeCents: int | None
    poolCents: int
    signedUp: int
    maxEntrants: int | None
    stage: str

class HostedEventPage(Out):
    events: list[HostedEventCard]
    nextCursor: str | None

class IssueOut(Out):
    id: str
    type: str
    label: str
    status: str
    matchId: str | None
    raisedBy: str | None
    data: dict[str, Any]
    createdAt: str | None
    claimedBy: str | None
    claimedAt: str | None
    closedBy: str | None
    closedAt: str | None

class QueueOut(Out):
    open: list[IssueOut]
    closed: list[IssueOut]

class ClaimOut(Out):
    matchId: str | None

class AdminInviteOut(Out):
    id: str
    eventId: str
    eventName: str
    game: str
    startsAt: str | None
    host: ProfileRef

class NotificationOut(Out):
    id: str
    type: str
    data: dict[str, Any]
    read: bool
    created_at: datetime

class MetaOut(Out):
    games: list[dict[str, Any]]
    formats: list[dict[str, Any]]
