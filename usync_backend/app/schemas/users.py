from pydantic import BaseModel, model_validator
from typing import Any, Literal

PLAYER_FIELDS = {
    "first_name",
    "last_name",
    "phone_number",
    "gender",
    "date_of_birth",
    "country",
    "interests",
    "other_games",
    "bracket_hosting",
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

class PlayerDetails(BaseModel):
    """
    
    """

    first_name: str
    last_name: str
    phone_number: str
    gender: str
    date_of_birth: str
    country: str
    interests: list[str] | None
    other_games: str | None
    bracket_hosting: str | None
    battlenet: str | None
    activision: str | None
    steam: str | None
    riot: str | None
    cmg: str | None
    gankster: str | None
    faceit: str | None
    battlefly: str | None

class HostDetails(BaseModel):
    """
    
    """

    hosted_games: list[str] | None
    other_hosted_games: str | None
    organization: str
    host_country: str
    event_types: list[str] | None

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
    bio: str
    twitch: str
    twitter: str
    youtube: str
    kick: str
    discord: str
    instagram: str
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
    