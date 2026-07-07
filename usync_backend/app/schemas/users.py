from pydantic import BaseModel
from typing import Any

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
    password: str

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
    