from pydantic import BaseModel
from datetime import datetime

class VerificationIn(BaseModel):
    """
    Validation class for payload information for the /verification endpoint.
    """

    username: str
    date_time: datetime
    tier: str
    email: str

class VerificationOut(BaseModel):
    """
    Validation class for the response payload information for the /verification endpoint.
    """
    
    message: str
