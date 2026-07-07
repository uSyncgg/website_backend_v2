from pydantic import BaseModel
from typing import Any, Literal

class FormReviewIn(BaseModel):
    """
    Validation class for payload information related to the event_forms /review endpoint.
    """
    
    form_data: dict[str, Any]

class FormReviewOut(BaseModel):
    """
    Validation class for response payload related to the event_forms /review endpoint.
    """

    message: str

class FormSubmissionIn(BaseModel):
    """
    Validation class for payload information related to event_forms /submission endpoint.
    """

    form_type: Literal["general"]
    form_data: dict[str, Any]

class FormSubmissionOut(BaseModel):
    """
    Validation class for response payload related to the event_forms /submission endpoint.
    """
    
    message: str
