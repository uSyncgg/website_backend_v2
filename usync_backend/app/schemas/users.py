from pydantic import BaseModel, model_validator, ConfigDict, StringConstraints, Field
from typing import Any, Literal, ClassVar, Annotated

PLAYER_FIELDS = {
    "first_name",
    "last_name",
    "phone_number",
    "gender",
    "date_of_birth",
    "country",
    "interests",
    "other_games",
    "battlenet",
    "activision",
    "steam",
    "riot",
    "cmg",
    "gankster",
    "faceit",
    "battlefly"
}

HOST_FIELDS = {
    "hosted_games",
    "other_hosted_games",
    "organization",
    "host_country",
    "event_types"
}

NONEMPTYSTR = Annotated[str, StringConstraints(min_length=1)]

class PlayerDetails(BaseModel):
    """
    
    """

    first_name: str
    last_name: str
    phone_number: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    country: str | None = None
    interests: list[str] | None = None
    other_games: str | None = None
    battlenet: str | None = None
    activision: str | None = None
    steam: str | None = None
    riot: str | None = None
    cmg: str | None = None
    gankster: str | None = None
    faceit: str | None = None
    battlefly: str | None = None

class HostDetails(BaseModel):
    """
    
    """

    hosted_games: list[str] | None = None
    other_hosted_games: str | None = None
    organization: str
    host_country: str
    event_types: list[str] | None = None

class RegistrationOut(BaseModel):
    """
    Validation class for the response payload information for the /register endpoint.
    """

    message: str

class RegistrationIn(BaseModel):
    """
    Validation class for payload information for the /register endpoint.
    """

    email: str
    username: str
    signup_path: list[Literal["player", "host"]]
    bio: str | None = None
    twitch: str | None = None
    twitter: str | None = None
    youtube: str | None = None
    kick: str | None = None
    discord: str | None = None
    instagram: str | None = None
    bracket_hosting: bool | None = None
    other_roles: list[str] | None = None
    other_role_detail: str | None = None
    venues: list[dict] | None = None
    player: PlayerDetails | None = None
    host: HostDetails | None = None

    @model_validator(mode = "before")
    @classmethod
    def sort_payload(cls, data):
        """
        
        """

        if not isinstance(data, dict):
            return data

        data = dict(data)
        roles = data.get("signup_path", [])

        if "player" in roles:
            data["player"] = {k: data.pop(k) for k in PLAYER_FIELDS if k in data}

        if "host" in roles:
            data["host"] = {k: data.pop(k) for k in HOST_FIELDS if k in data}

        return data

    @model_validator(mode = "after")
    def check_roles(self):
        """
        
        """

        if ("player" in self.signup_path) != (self.player is not None):
            raise ValueError("player details required if 'player' is in sign up path")

        if ("host" in self.signup_path) != (self.host is not None):
            raise ValueError("host details required if 'host' is in sign up path")

        return self

class Profile(BaseModel):
    """
    
    """

    username: str
    verified: bool
    is_player: bool
    is_host: bool
    bio: str | None = None
    other_roles: list[str] | None = None
    other_role_detail: str | None = None
    games: list[str] | None = None
    organization: str | None = None
    host_games: list[str] | None = None
    event_types: list[str] | None = None
    venues: list[dict] | None = None

class Me(BaseModel):
    """
    Validation class for the response payload information for the /me endpoint.
    """

    username: str
    profile_picture: str | None = None

class ProfileSettingsOut(BaseModel):
    """
    
    """

    username: str
    verified: bool
    is_player: bool
    is_host: bool
    bio: str | None = None
    bracket_hosting: bool
    other_roles: list[str] | None = None
    other_role_detail: str | None = None
    profile_picture: str | None = None

    games: list[str] | None = None
    other_games: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    country: str | None = None

    organization: str | None = None
    host_games: list[str] | None = None
    host_other_games: str | None = None
    host_country: str | None = None
    event_types: list[str] | None = None

    venues: list[dict] | None = None

class Updater(BaseModel):
    """
    
    """

    never_null: ClassVar[set[str]] = set()

    model_config = ConfigDict(extra = "forbid", str_strip_whitespace=True)

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

class UserUpdate(Updater):
    """
    
    """

    never_null: ClassVar[set[str]] = {
        "bracket_hosting"
    }

    bio: str | None = None
    twitch: str | None = None
    twitter: str | None = None
    youtube: str | None = None
    kick: str | None = None
    discord: str | None = None
    instagram: str | None = None
    bracket_hosting: bool | None = None
    other_roles: list[str] | None = None
    other_role_detail: str | None = None

class PlayerUpdate(Updater):
    """
    
    """

    never_null: ClassVar[set[str]] = {
        "first_name",
        "last_name",
        "activision",
        "battlenet",
        "steam",
        "riot",
        "cmg",
        "gankster",
        "faceit",
        "battlefly"
    }

    first_name: NONEMPTYSTR | None = None
    last_name: NONEMPTYSTR | None = None
    phone_number: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    country: str | None = None
    interests: list[str] | None = Field(default=None, serialization_alias="games")
    other_games: str | None = None
    battlenet: str | None = None
    activision: str | None = None
    steam: str | None = None
    riot: str | None = None
    cmg: str | None = None
    gankster: str | None = None
    faceit: str | None = None
    battlefly: str | None = None

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
    