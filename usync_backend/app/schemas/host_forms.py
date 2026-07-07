from pydantic import BaseModel
from typing import Any

class HostFormIn(BaseModel):
    """
    Validation class for payload information for the /hostform endpoint.
    """

    form_type: str
    payload: dict[str, Any]

class HostFormOut(BaseModel):
    """
    Validation class for the response payload information for the /hostform endpoint.
    """

    message: str
    