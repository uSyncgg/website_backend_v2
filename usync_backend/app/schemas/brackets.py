import uuid

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

# Request bodies for /brackets. The frontend speaks camelCase (it's the same shape the bracket
# pages already use), so fields take camelCase aliases and also accept snake_case.

class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator = to_camel, populate_by_name = True, extra = "ignore")

class SignupQuestion(CamelModel):
    label: str = Field(max_length = 120)
    type: str = "text"
    options: str = ""
    required: bool = False

class SignupForm(CamelModel):
    player_fields: list[str] = Field(default_factory = list, max_length = 10)
    questions: list[SignupQuestion] = Field(default_factory = list, max_length = 20)

class EventCreate(CamelModel):
    host_profile: str
    name: str = Field(min_length = 1, max_length = 80)
    event_type: str = Field(pattern = "^(league|lan|tournament)$")
    game: str = Field(min_length = 1, max_length = 60)
    format: str = Field(pattern = "^(single_elim|double_elim|round_robin)$")
    team_size: str = Field(pattern = "^([1-9]|10)v([1-9]|10)$")
    best_of: int = Field(ge = 1, le = 9)
    max_entrants: int | None = Field(default = None, ge = 2, le = 1024)
    starts_at: datetime
    location_type: str = Field(default = "online", pattern = "^(online|in_person)$")
    venue: str = Field(default = "", max_length = 200)
    entry_fee: str = Field(default = "Free", max_length = 40)
    prize: str = Field(default = "", max_length = 200)
    description: str = Field(default = "", max_length = 5000)
    rules: str = Field(default = "", max_length = 5000)
    registration_open: bool = True
    no_show_minutes: int | None = Field(default = None, ge = 0, le = 240)
    payments: dict = Field(default_factory = dict)
    signup_form: SignupForm = Field(default_factory = SignupForm)

class EventUpdate(CamelModel):
    name: str | None = Field(default = None, min_length = 1, max_length = 80)
    description: str | None = Field(default = None, max_length = 5000)
    rules: str | None = Field(default = None, max_length = 5000)
    prize: str | None = Field(default = None, max_length = 200)
    venue: str | None = Field(default = None, max_length = 200)
    best_of: int | None = Field(default = None, ge = 1, le = 9)
    max_entrants: int | None = Field(default = None, ge = 2, le = 1024)
    registration_open: bool | None = None
    no_show_minutes: int | None = Field(default = None, ge = 0, le = 240)

class EventDelay(CamelModel):
    starts_at: datetime
    reason: str = Field(default = "", max_length = 300)

class Registration(CamelModel):
    team_id: str | None = None
    email: str = Field(max_length = 200)
    answers: dict[str, str] = Field(default_factory = dict)
    pass_name: str | None = Field(default = None, alias = "pass", max_length = 80)

    @field_validator("email")
    @classmethod
    def email_shape(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1] or " " in v:
            raise ValueError("Enter a valid email.")
        return v

class SeedsUpdate(CamelModel):
    seeds: list[str] = Field(max_length = 1024)

class MatchScore(CamelModel):
    score_a: int = Field(ge = 0, le = 99)
    score_b: int = Field(ge = 0, le = 99)
    forfeit: bool = False

class NotificationsRead(BaseModel):
    ids: list[uuid.UUID] | None = None
