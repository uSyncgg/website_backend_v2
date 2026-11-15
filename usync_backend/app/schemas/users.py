import re

from datetime import date, datetime
from pydantic import BaseModel, model_validator, ConfigDict, StringConstraints, Field, BeforeValidator, AfterValidator, PlainSerializer
from typing import Any, Literal, ClassVar, Annotated

NONEMPTYSTR = Annotated[str, StringConstraints(min_length=1)]

ProfileType = Literal["player", "host", "venue", "org"]
# Socials plus competitive site accounts (CMG, Gankster, FACEIT, Battlefy).
LinkPlatform = Literal["twitch", "twitter", "youtube", "kick", "discord", "instagram", "cmg", "gankster", "faceit", "battlefy"]
PictureSource = Literal["upload", "copy_default"]

# other_roles is a set (no duplicates) that leaves as a sorted list, so the
# ARRAY column and API responses get a stable order. Requests only accept the
# fixed choices; responses take whatever is stored so an old value can't 500.
# Venue and Organization (formerly "Team") aren't choices: each is a profile type of its own.
OtherRole = Literal["Coach", "Sponsor", "Viewer", "Other"]
_SORTED = PlainSerializer(sorted, return_type=list[str])
OtherRolesIn = Annotated[set[OtherRole], _SORTED]
OtherRolesOut = Annotated[set[str], _SORTED]

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_BIRTH_DATE = date(1900, 1, 1)

def _parse_birth_date(value: Any) -> Any:
    """
    Only accepts a YYYY-MM-DD string (what a browser date input sends) or a date. A blank
    input means "not provided". Pydantic would otherwise also accept timestamps and the like.
    """

    if value is None or isinstance(value, date):
        return value

    if isinstance(value, str):
        value = value.strip()

        if value == "":
            return None

        if _ISO_DATE_RE.match(value):
            return value

    raise ValueError("Date of birth must be in YYYY-MM-DD format.")

def _check_birth_date_range(value: date | None) -> date | None:
    if value is not None and not (MIN_BIRTH_DATE <= value <= date.today()):
        raise ValueError("Enter a real date of birth.")

    return value

BirthDate = Annotated[date | None, BeforeValidator(_parse_birth_date), AfterValidator(_check_birth_date_range)]

def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip() == "":
        return None

    return value

def _clean_handle(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().removeprefix("@").strip()

    return value

# A linked account handle, stored without a leading @ (so "@SamTV" and "SamTV" are the same).
LinkHandle = Annotated[str, BeforeValidator(_clean_handle), StringConstraints(min_length=1)]

# Game account IDs are unique across players, so a blank one is stored as NULL (Postgres
# allows any number of NULLs in a unique column, but only one "").
GameAccount = Annotated[str | None, BeforeValidator(_blank_to_none)]

class StrictIn(BaseModel):
    """
    Base for request bodies: unknown fields are a 422 and strings are trimmed.
    """

    model_config = ConfigDict(extra = "forbid", str_strip_whitespace=True)

class VenueIn(StrictIn):
    """
    One venue location. A venue profile's locations are replaced as a whole set on update.
    """

    name: NONEMPTYSTR
    location: NONEMPTYSTR

class PlayerDetails(StrictIn):
    """
    The details of a player profile, sent at sign up or when adding the profile.
    """

    first_name: NONEMPTYSTR
    last_name: NONEMPTYSTR
    phone_number: str | None = None
    gender: str | None = None
    date_of_birth: BirthDate = None
    country: str | None = None
    interests: list[str] | None = None
    other_games: str | None = None
    battlenet: GameAccount = None
    activision: GameAccount = None
    steam: GameAccount = None
    riot: GameAccount = None

class HostDetails(StrictIn):
    """
    The details of a host profile, sent at sign up or when adding the profile.
    """

    hosted_games: list[str] | None = None
    other_hosted_games: str | None = None
    organization: NONEMPTYSTR
    host_country: NONEMPTYSTR
    event_types: list[str] | None = None

TEAM_NAME = Annotated[str, StringConstraints(min_length=2, max_length=40)]
MAX_TEAMS_AT_SIGN_UP = 20

class TeamIn(StrictIn):
    """
    A team to create under an org profile. Its name is unique within the org (ignoring case).
    """

    name: TEAM_NAME
    game: str | None = None

class OrgDetails(StrictIn):
    """
    The details of an org profile, sent at sign up or when adding the profile. Its name is the
    profile's username, so `organization` isn't sent. `teams` is optional: an org can create
    its teams now or later from its profile.
    """

    org_country: NONEMPTYSTR
    games: list[str] | None = None
    other_games: str | None = None
    teams: list[TeamIn] = Field(default_factory=list, max_length=MAX_TEAMS_AT_SIGN_UP)

    @model_validator(mode = "after")
    def unique_team_names(self):
        names = [t.name.casefold() for t in self.teams]

        if len(names) != len(set(names)):
            raise ValueError("Each team in an organization needs a different name.")

        return self

class VenueDetails(StrictIn):
    """
    The details of a venue profile: its locations, at least one.
    """

    locations: list[VenueIn] = Field(min_length=1)

class ProfileBase(StrictIn):
    """
    The fields every profile type shares. `picture` says where the profile picture comes from:
    "upload" (a file sent alongside, named profile_picture_<type> at sign up), "copy_default"
    (a copy of the default profile's picture, without moderating it again) or none.
    """

    username: str
    picture: PictureSource | None = None
    bio: str | None = None
    bracket_hosting: bool = True
    other_roles: OtherRolesIn | None = None
    other_role_detail: str | None = None
    links: dict[LinkPlatform, LinkHandle] | None = None

class PlayerProfileIn(ProfileBase):
    type: Literal["player"]
    details: PlayerDetails

class HostProfileIn(ProfileBase):
    type: Literal["host"]
    details: HostDetails

class VenueProfileIn(ProfileBase):
    type: Literal["venue"]
    details: VenueDetails

class OrgProfileIn(ProfileBase):
    type: Literal["org"]
    details: OrgDetails

# `type` picks the model, so a player profile must send player details and so on.
ProfileIn = Annotated[PlayerProfileIn | HostProfileIn | VenueProfileIn | OrgProfileIn, Field(discriminator="type")]

class RegistrationOut(BaseModel):
    """
    Validation class for the response payload information for the /register endpoint.
    """

    message: str

class RegistrationIn(StrictIn):
    """
    Validation class for payload information for the /register endpoint: one entry per profile
    the user is creating, and which of them is their default.
    """

    default_profile: ProfileType
    profiles: list[ProfileIn] = Field(min_length=1, max_length=4)

    @model_validator(mode = "after")
    def check_profiles(self):
        """
        One profile per type, the default must be one of them, and only other profiles can copy
        the default's picture.
        """

        types = [p.type for p in self.profiles]

        if len(types) != len(set(types)):
            raise ValueError("Only one profile of each type can be created.")

        if self.default_profile not in types:
            raise ValueError("The default profile must be one of the profiles being created.")

        default = next(p for p in self.profiles if p.type == self.default_profile)

        if default.picture == "copy_default":
            raise ValueError("The default profile can't copy its own picture.")

        if default.picture != "upload" and any(p.picture == "copy_default" for p in self.profiles):
            raise ValueError("The default profile needs a picture for others to copy it.")

        return self

class ProfileSummary(BaseModel):
    """
    The minimum needed to show a profile in a list or link to it (/profile/<public_id>/<username>).
    """

    type: ProfileType
    username: str
    public_id: str
    profile_picture: str | None = None

class Me(BaseModel):
    """
    Validation class for the response payload information for the /me endpoint: the navbar's
    default profile and every profile on the account for the view switcher.
    """

    verified: bool
    default_profile: ProfileSummary | None = None
    profiles: list[ProfileSummary] = []

class VenueOut(BaseModel):
    name: str
    location: str

class PlayerPublic(BaseModel):
    games: list[str] = []

class HostPublic(BaseModel):
    organization: str
    hosted_games: list[str] = []
    event_types: list[str] = []

class VenuePublic(BaseModel):
    locations: list[VenueOut] = []

class TeamMemberOut(BaseModel):
    """
    A player on a team: their player profile's summary and role ("captain" or "player").
    """

    public_id: str
    username: str
    profile_picture: str | None = None
    role: str

class TeamInviteOut(BaseModel):
    id: str
    public_id: str
    username: str
    profile_picture: str | None = None
    created_at: datetime

class TeamOut(BaseModel):
    """
    A team. `owner` is the profile that runs it (an org, or the player who made it).
    `invites` (pending only) is only filled in for the owner. `my_role` is the signed in
    user's place on it ("owner", "captain", "player" or None), where that's known.
    """

    public_id: str
    name: str
    game: str | None = None
    picture: str | None = None
    created_at: datetime
    owner: "ProfileSummary"
    my_role: str | None = None
    members: list[TeamMemberOut] = []
    invites: list[TeamInviteOut] = []

class OrgPublic(BaseModel):
    organization: str
    games: list[str] = []
    teams: list[TeamOut] = []

class Profile(BaseModel):
    """
    A public profile page. `others` lists the account's other profiles (from the profiles table
    only); their details are fetched when the viewer switches to one.
    """

    public_id: str
    type: ProfileType
    username: str
    verified: bool
    bio: str | None = None
    profile_picture: str | None = None
    other_roles: OtherRolesOut = set()
    other_role_detail: str | None = None
    links: dict[str, str] = {}
    details: PlayerPublic | HostPublic | VenuePublic | OrgPublic
    others: list[ProfileSummary] = []

class PlayerSettings(BaseModel):
    first_name: str
    last_name: str
    phone_number: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    country: str | None = None
    interests: list[str] = []
    other_games: str | None = None
    battlenet: str | None = None
    activision: str | None = None
    steam: str | None = None
    riot: str | None = None

class HostSettings(BaseModel):
    organization: str
    hosted_games: list[str] = []
    other_hosted_games: str | None = None
    host_country: str
    event_types: list[str] = []

class VenueSettings(BaseModel):
    locations: list[VenueOut] = []

class OrgSettings(BaseModel):
    organization: str
    org_country: str
    games: list[str] = []
    other_games: str | None = None

class ProfileSettingsOut(BaseModel):
    """
    Everything the owner can edit on one of their profiles.
    """

    public_id: str
    type: ProfileType
    username: str
    verified: bool
    is_default: bool
    bio: str | None = None
    profile_picture: str | None = None
    bracket_hosting: bool
    other_roles: OtherRolesOut = set()
    other_role_detail: str | None = None
    links: dict[str, str] = {}
    details: PlayerSettings | HostSettings | VenueSettings | OrgSettings

class Updater(StrictIn):
    """
    Base for partial updates. Fields listed in never_null can't be cleared, so a null for one
    of them is dropped (treated as "not sent").
    """

    never_null: ClassVar[set[str]] = set()

    @model_validator(mode="before")
    @classmethod
    def check_fields(cls, data) -> Any:
        """

        """

        if not isinstance(data, dict):
            return data

        data = dict(data)

        for key in cls.never_null:
            if key in data and data[key] is None:
                data.pop(key)

        return data

class PlayerUpdate(Updater):
    """

    """

    never_null: ClassVar[set[str]] = {
        "first_name",
        "last_name"
    }

    first_name: NONEMPTYSTR | None = None
    last_name: NONEMPTYSTR | None = None
    phone_number: str | None = None
    gender: str | None = None
    date_of_birth: BirthDate = None
    country: str | None = None
    interests: list[str] | None = Field(default=None, serialization_alias="games")
    other_games: str | None = None
    battlenet: GameAccount = None
    activision: GameAccount = None
    steam: GameAccount = None
    riot: GameAccount = None

class HostUpdate(Updater):
    """

    """

    never_null: ClassVar[set[str]] = {
        "organization",
        "host_country"
    }

    hosted_games: list[str] | None = Field(default=None, serialization_alias="games")
    other_hosted_games: str | None = Field(default = None, serialization_alias="other_games")
    organization: NONEMPTYSTR | None = None
    host_country: NONEMPTYSTR | None = None
    event_types: list[str] | None = None

class VenueUpdate(Updater):
    """

    """

    never_null: ClassVar[set[str]] = {
        "locations"
    }

    locations: list[VenueIn] | None = Field(default=None, min_length=1)

class OrgUpdate(Updater):
    """

    """

    never_null: ClassVar[set[str]] = {
        "org_country"
    }

    # The name changes with the username (PATCH .../org/username), not here.
    org_country: NONEMPTYSTR | None = None
    games: list[str] | None = None
    other_games: str | None = None

class ProfileUpdate(Updater):
    """
    Partial update of one of the user's profiles. `links`, when sent, replaces the whole set.
    `details` is checked against the profile's type (PlayerUpdate, HostUpdate, VenueUpdate or OrgUpdate).
    """

    never_null: ClassVar[set[str]] = {
        "bracket_hosting",
        "links",
        "details"
    }

    bio: str | None = None
    bracket_hosting: bool | None = None
    other_roles: OtherRolesIn | None = None
    other_role_detail: str | None = None
    links: dict[LinkPlatform, LinkHandle] | None = None
    details: dict[str, Any] | None = None

class UsernameUpdate(StrictIn):
    """
    Validation class for payload information for the PATCH /me/profiles/{type}/username endpoint.
    """

    username: str

class DefaultProfileUpdate(StrictIn):
    """
    Validation class for payload information for the PATCH /me/default-profile endpoint.
    """

    type: ProfileType

class TeamCreate(TeamIn):
    """
    POST /teams (the `data` field; the picture is a file alongside). `owner_type` picks which of
    the account's profiles owns it. `invites` are player usernames to invite straight away.
    """

    owner_type: ProfileType
    invites: list[NONEMPTYSTR] = Field(default_factory=list, max_length=20)

class TeamUpdate(Updater):
    """
    PATCH /teams/{team_public_id}. A null game clears it.
    """

    never_null: ClassVar[set[str]] = {"name"}

    name: TEAM_NAME | None = None
    game: str | None = None

class TeamInviteCreate(StrictIn):
    """
    POST /teams/{team_public_id}/invites: the player profile's username.
    """

    username: NONEMPTYSTR

class TeamRoleUpdate(StrictIn):
    role: Literal["captain", "player"]

class MyTeamInviteOut(BaseModel):
    """
    A pending invite as the invited player sees it: the team they'd join (with its roster) and
    who invited them (the team's owner).
    """

    id: str
    team: TeamOut
    invited_by: "ProfileSummary"
    created_at: datetime

class UpdateProfileIn(BaseModel):
    """
    Validation class for payload information for the /profile/update endpoint.
    """

    payload: dict[str, Any]

class UpdateProfileOut(BaseModel):
    """
    Validation class for the response payload information for the /profile/update endpoint.
    """

    message: str

TeamOut.model_rebuild()
MyTeamInviteOut.model_rebuild()
