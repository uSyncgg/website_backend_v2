from pydantic import BaseModel
from typing import Any

class CodTournamentOut(BaseModel):
    """
    Validation class for the response payload information related to the /tournaments/{game} endpoint.
    """
    
    date: str
    time: str
    entry: str
    region: str
    platforms: str
    requirements: str
    skill: str
    team_size: str
    gamemode: str
    series: str
    url: str
    site: str
    is_1v1: bool 
    is_2v2: bool
    is_3v3: bool
    is_4v4: bool
    is_na: bool
    is_eu: bool
    is_latam: bool
    is_usa: bool
    is_apac: bool
    is_worldwide: bool
    is_pc: bool
    is_console: bool
    is_all_platforms: bool
    is_novice: bool
    is_amateur: bool
    is_expert: bool
    is_agent: bool
    is_master: bool
    is_challenger: bool
    is_all_skill: bool
    is_free: bool
    is_paid: bool
    is_eco: bool
    is_elite: bool
    