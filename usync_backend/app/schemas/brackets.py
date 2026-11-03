import re
import uuid

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.schemas.users import ProfileSummary
from app.services import GAMES

# Events are for the site's supported titles only (no "Other"), the same list the frontend form offers.
BRACKET_GAMES = tuple(GAMES)
PLAYER_FIELD_OPTIONS = ("Activision ID", "Riot ID", "Epic ID", "Steam", "Battle.net", "Xbox Gamertag", "PSN", "Discord", "Twitter / X")

# Same as the site's sections (and ProfileEvent.kind for listed events).
BracketEventType = Literal["league", "lan", "wager", "h2h"]
BracketFormat = Literal["single_elim", "double_elim", "round_robin"]
BracketLocation = Literal["online", "in_person"]
BracketStage = Literal["registration", "closed", "seeding", "live", "complete"]
BracketDecision = Literal["host", "players", "forfeit", "cleared"]

_EMAIL = re.compile(r"^\S+@\S+\.\S+$")
# Match ids the bracket engine produces: r0m3, l2m1, gf1, gf2, rr4m0.
_MATCH_ID = re.compile(r"^(r\d+m\d+|l\d+m\d+|gf[12]|rr\d+m\d+)$")

def check_match_id(match_id: str) -> str:
    if not _MATCH_ID.match(match_id):
        raise ValueError("Unknown match.")
    return match_id

class StrictIn(BaseModel):
    """
    Request bodies reject fields they don't know, so a typo can't be silently dropped.
    """

    model_config = ConfigDict(extra = "forbid", str_strip_whitespace = True)

# ---- sign-up form and payments ----------------------------------------------

class SignupQuestion(StrictIn):
    label: str = Field(min_length = 1, max_length = 120)
    type: Literal["text", "select"] = "text"
    options: list[str] = []
    required: bool = False

    @field_validator("options", mode = "before")
    @classmethod
    def split_options(cls, value):
        # The form types them as "PC, PlayStation, Xbox".
        if isinstance(value, str):
            value = value.split(",")
        return [o.strip() for o in value if o and o.strip()]

    @model_validator(mode = "after")
    def select_needs_options(self):
        if self.type == "select" and not self.options:
            raise ValueError(f"'{self.label}' is a dropdown, so it needs options.")
        return self

class SignupForm(StrictIn):
    player_fields: list[str] = ["Discord"]
    questions: list[SignupQuestion] = Field(default = [], max_length = 20)

    @field_validator("player_fields")
    @classmethod
    def known_fields(cls, value: list[str]) -> list[str]:
        unknown = [f for f in value if f not in PLAYER_FIELD_OPTIONS]
        if unknown:
            raise ValueError(f"Unknown player fields: {', '.join(unknown)}")
        return list(dict.fromkeys(value))

    @model_validator(mode = "after")
    def unique_labels(self):
        labels = [q.label.lower() for q in self.questions] + [f.lower() for f in self.player_fields]
        if len(labels) != len(set(labels)):
            raise ValueError("Each question needs its own label.")
        return self

class PassTierIn(StrictIn):
    name: str = Field(min_length = 1, max_length = 60)
    price_cents: int = Field(gt = 0)
    capacity: int | None = Field(default = None, ge = 1)

class PaymentsIn(StrictIn):
    enabled: bool = False
    notify_email: str | None = None
    tiers: list[PassTierIn] = []

    @model_validator(mode = "after")
    def paid_needs_details(self):
        if not self.enabled:
            return self
        if not self.notify_email or not _EMAIL.match(self.notify_email):
            raise ValueError("Add the email we should notify about new sign-ups.")
        if not self.tiers:
            raise ValueError("A paid event needs at least one pass.")
        if len({t.name.lower() for t in self.tiers}) != len(self.tiers):
            raise ValueError("Each pass needs its own name.")
        return self

# ---- host: create and edit --------------------------------------------------

class BracketEventFields(StrictIn):
    """
    The fields a host sets that can also be edited later. Used whole on create and as a partial
    on update (only the fields sent are changed).
    """

    name: str = Field(min_length = 1, max_length = 80)
    game: str
    best_of: Literal[1, 3, 5, 7] = 3
    max_entrants: int | None = Field(default = 16, ge = 2)
    location_type: BracketLocation = "online"
    venue: str | None = Field(default = None, max_length = 200)
    description: str = Field(default = "", max_length = 1000)
    rules: str = Field(default = "", max_length = 3000)
    prize: str | None = Field(default = None, max_length = 120)
    check_in_required: bool = True
    no_show_minutes: int | None = Field(default = 10, ge = 0, le = 240)

    @field_validator("game")
    @classmethod
    def known_game(cls, value: str) -> str:
        if value not in BRACKET_GAMES:
            raise ValueError("Pick a game from the list.")
        return value

    @field_validator("venue", "prize")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return value or None

class BracketEventIn(BracketEventFields):
    """
    POST /brackets. host_profile is the public_id of the account's host or venue profile the
    event is posted as; it's listed on that profile.
    """

    host_profile: str
    event_type: BracketEventType
    format: BracketFormat = "single_elim"
    team_size: int = Field(default = 1, ge = 1, le = 5)
    starts_at: datetime
    signup_form: SignupForm = SignupForm()
    payments: PaymentsIn = PaymentsIn()

    @model_validator(mode = "after")
    def venue_for_lans(self):
        if self.event_type == "lan" and self.location_type != "in_person":
            raise ValueError("LANs are held in person.")
        if self.location_type == "in_person" and not self.venue:
            raise ValueError("Add where the event is held.")
        return self

class BracketEventUpdate(BracketEventFields):
    """
    PATCH /brackets/{id}: any of these. Format, team size, sign-up form and passes are fixed once
    the event is created (entrants signed up against them).
    """

    name: str | None = Field(default = None, min_length = 1, max_length = 80)
    event_type: BracketEventType | None = None
    game: str | None = None
    best_of: Literal[1, 3, 5, 7] | None = None
    location_type: BracketLocation | None = None
    description: str | None = Field(default = None, max_length = 1000)
    rules: str | None = Field(default = None, max_length = 3000)
    check_in_required: bool | None = None
    registration_open: bool | None = None

    @field_validator("game")
    @classmethod
    def known_game(cls, value: str | None) -> str | None:
        if value is not None and value not in BRACKET_GAMES:
            raise ValueError("Pick a game from the list.")
        return value

class DelayIn(StrictIn):
    starts_at: datetime
    reason: str = Field(default = "", max_length = 200)

# ---- entrants and registration ---------------------------------------------

class HostEntrantIn(StrictIn):
    """
    The host adding a player or team by name (no account, no payment).
    """

    name: str = Field(min_length = 1, max_length = 40)

class CheckInIn(StrictIn):
    checked_in: bool

class RegistrationIn(StrictIn):
    """
    A signed-in player signing up (POST /brackets/{id}/registration). For team events `name` is
    the team name and `captain` the captain's gamertag; for 1v1 the captain is the player.
    """

    name: str = Field(min_length = 1, max_length = 40)
    captain: str | None = Field(default = None, max_length = 40)
    email: str
    teammates: list[str] = []
    answers: dict[str, str] = {}
    pass_tier_id: uuid.UUID | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        if not _EMAIL.match(value):
            raise ValueError("Enter a valid email.")
        return value

    @field_validator("teammates")
    @classmethod
    def trim_teammates(cls, value: list[str]) -> list[str]:
        return [t.strip()[:40] for t in value if t and t.strip()]

class RegistrationOut(BaseModel):
    """
    registration_id is set for paid events: the frontend continues to
    POST /event-registration/{registration_id}/payment-intent, like the pass portal.
    """

    entrant_id: uuid.UUID
    registration_id: uuid.UUID | None = None
    total_snapshot_cents: int | None = None

# ---- bracket ----------------------------------------------------------------

class SeedsIn(StrictIn):
    seeds: list[uuid.UUID] = Field(min_length = 2)

    @field_validator("seeds")
    @classmethod
    def no_repeats(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Each entrant can only be seeded once.")
        return value

class ScoreIn(StrictIn):
    score_a: int = Field(ge = 0)
    score_b: int = Field(ge = 0)

    @model_validator(mode = "after")
    def has_winner(self):
        if self.score_a == self.score_b:
            raise ValueError("A match needs a winner.")
        return self

class HostResultIn(ScoreIn):
    forfeit: bool = False

class ReportOut(BaseModel):
    status: Literal["confirmed", "disputed", "waiting"]

# ---- responses --------------------------------------------------------------

class PassTierOut(BaseModel):
    id: uuid.UUID
    name: str
    price_cents: int
    capacity: int | None = None
    sold_count: int = 0

class PaymentsOut(BaseModel):
    enabled: bool
    tiers: list[PassTierOut] = []
    # Only on the host's view.
    notify_email: str | None = None

class DelayOut(BaseModel):
    from_starts_at: datetime
    to_starts_at: datetime
    reason: str
    at: datetime

class EntrantPublic(BaseModel):
    id: uuid.UUID
    name: str
    captain: str
    checked_in: bool

class EntrantHost(EntrantPublic):
    teammates: list[str] = []
    email: str | None = None
    answers: dict = {}
    rating: int
    registered_at: datetime
    # 'manual' (added by the host), 'free', or the registration's payment_status for paid sign-ups.
    payment_status: str
    account_linked: bool

class ResultOut(BaseModel):
    a: uuid.UUID
    b: uuid.UUID
    score_a: int
    score_b: int
    forfeit: bool = False

class ReportRecord(BaseModel):
    a: uuid.UUID
    b: uuid.UUID
    score_a: int
    score_b: int
    at: datetime

class NoShowOut(BaseModel):
    a: uuid.UUID
    b: uuid.UUID
    reported_by: uuid.UUID
    against: uuid.UUID
    at: datetime

class LogEntryOut(BaseModel):
    id: uuid.UUID
    match_id: str
    a: uuid.UUID
    b: uuid.UUID
    score_a: int | None
    score_b: int | None
    source: BracketDecision
    at: datetime
    reverted: bool
    reverted_at: datetime | None = None

class BracketOut(BaseModel):
    """
    Seeds plus the stored results, reports and no-shows, keyed by match id - everything the
    frontend's buildBracket / matchState need. Stale records (for a matchup that no longer
    applies) are left in; the frontend ignores them the same way the server does.
    """

    format: BracketFormat
    seeds: list[uuid.UUID]
    published: bool
    generated_at: datetime
    results: dict[str, ResultOut] = {}
    reports: dict[str, dict[str, ReportRecord]] = {}
    no_shows: dict[str, NoShowOut] = {}
    placements: list[uuid.UUID | None] | None = None
    # Only on the host's view.
    log: list[LogEntryOut] | None = None

class BracketEventOut(BaseModel):
    """
    An event as players see it (GET /brackets/{id}); the host's view (GET /brackets/{id}/manage)
    fills in the entrant details, the sign-up notification email, the unpublished bracket and the decision log.
    """

    id: uuid.UUID
    host_profile: ProfileSummary
    name: str
    event_type: BracketEventType
    game: str
    format: BracketFormat
    team_size: int
    best_of: int
    max_entrants: int | None
    starts_at: datetime
    location_type: BracketLocation
    venue: str | None
    description: str
    rules: str
    prize: str | None
    entry_fee: str
    check_in_required: bool
    no_show_minutes: int | None
    registration_open: bool
    stage: BracketStage
    signup_form: SignupForm
    payments: PaymentsOut
    delays: list[DelayOut] = []
    entrant_count: int
    entrants: list[EntrantHost] | list[EntrantPublic] = []
    bracket: BracketOut | None = None
    # The signed-in viewer's own entrant, if they're signed up.
    viewer_entrant_id: uuid.UUID | None = None
    created_at: datetime

class BracketEventSummary(BaseModel):
    """
    A row on the Host Dashboard.
    """

    id: uuid.UUID
    host_profile: ProfileSummary
    name: str
    event_type: BracketEventType
    game: str
    format: BracketFormat
    team_size: int
    starts_at: datetime
    stage: BracketStage
    entrant_count: int
    max_entrants: int | None
    created_at: datetime
