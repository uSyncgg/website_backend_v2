import uuid

from pydantic import BaseModel
from typing import Any

class PaymentFormIn(BaseModel):
    """
    Validation class for payload information for the /payment/event endpoint.
    """

    id: str
    amount: int
    team_name: str
    event_name: str
    user_id: uuid.UUID

class PaymentFormOut(BaseModel):
    """
    Validation class for the response payload information for the /payment/event endpoint.
    """

    message: str
