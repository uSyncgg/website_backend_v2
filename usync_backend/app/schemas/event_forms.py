from pydantic import BaseModel
from http import HTTPStatus

class FormReviewIn(BaseModel):
    """
    Validation class for payload information related to the event_forms /review endpoint.
    """
    
    team_name: str
    unique_field_names: list[str]
    form_data: dict[str, str | int]

class FormReviewOut(BaseModel):
    """
    Validation class for response payload related to the event_forms /review endpoint.
    """

    status: HTTPStatus
    invalid_field_names: list[str] = []

class FormSubmissionIn(BaseModel):
    """
    Validation class for payload information related to event_forms /submission endpoint.
    """

    form_data: dict[str, str | int]
    form_type: str = "general"

class FormSubmissionOut(BaseModel):
    """
    Validation class for response payload related to the event_forms /submission endpoint.
    """
    
    status: HTTPStatus
    error_response: str = ""
