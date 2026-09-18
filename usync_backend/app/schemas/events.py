from pydantic import BaseModel, ConfigDict
from typing import Any

class LeaguesOut(BaseModel):
    """
    Validation class for the response payload information related to the /events/leagues endpoint.
    """
    model_config = ConfigDict(from_attributes=True)

    name: str
    seasonality: str
    details: list[str]
    region: str
    team_size: str
    fee_details: list[str]
    url: str
    header_img: str
    banner_img: str
    verified: bool
    path: str
    group: str | None
    is_hs: bool
    is_college: bool
    seo_title: str | None
    seo_description: str | None
    game: str

class LeagueParentsOut(BaseModel):
    """
    Validation class for the parent response payload information related to leagues.
    """
    model_config = ConfigDict(from_attributes=True)

    name: str
    banner_img: str
    header_img: str
    verified: bool
    path: str
    game: str
    leagues: list[LeaguesOut] = []
    is_hs: bool
    is_college: bool
    seo_title: str | None
    seo_description: str | None
    game: str

class LansOut(BaseModel):
    """
    Validation class for the response payload information related to the /events/lans endpoint.
    """
    model_config = ConfigDict(from_attributes=True)

    name: str
    date: str
    location: str
    details: list[str]
    fee_details: list[str]
    url: str
    header_img: str
    banner_img: str
    verified: bool
    archived: bool
    path: str
    lat: float
    long: float
    game: str
    seo_title: str | None
    seo_description: str | None
    usync_pass: bool
    game: str

class WagersOut(BaseModel):
    """
    Validation class for the response payload information related to the /events/wagers endpoint.
    """
    model_config = ConfigDict(from_attributes=True)

    name: str
    fee_details: list[str]
    details: list[str]
    restrictions: dict[str, str | list[str]]
    availability: list[str]
    url: str
    header_img: str
    banner_img: str
    verified: bool
    path: str
    seo_title: str | None
    seo_description: str | None
    game: str

class XpsOut(BaseModel):
    """
    Validation class for the response payload information related to the /events/h2h endpoint.
    """
    model_config = ConfigDict(from_attributes=True)

    name: str
    details: list[str]
    fee_details: list[str]
    restrictions: dict[str, str | list[str]]
    availability: list[str]
    url: str
    header_img: str
    banner_img: str
    verified: bool
    path: str
    seo_title: str | None
    seo_description: str | None
    game: str
