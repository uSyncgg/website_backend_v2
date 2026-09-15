import uuid

from pydantic import BaseModel

class EventPassesOut(BaseModel):
    """
    Validation class for EventPasses being sent to the frontend.
    """

    id: uuid.UUID
    tier_name: str
    price_cents: int
    is_active: bool
    capacity: int | None
    sold_count: int
    requires_team: bool
    form_fields: list[dict]
    player_fields: list[dict]
    team_size: int

class EventSubmissionIn(BaseModel):
    """
    Validation class for the EventSubmission payload from the frontend.
    """

    pass_tier_id: uuid.UUID
    team_name: str | None
    contact_username: str | None
    contact_email: str
    player_info: list[dict]
    custom_fields: dict
    org_twitter: str | None

class EventSubmissionOut(BaseModel):
    """
    Validation class for the EventSubmission information being sent to the frontend.
    """

    registration_id: uuid.UUID
    status: str = "pending"
    total_snapshot_cents: int

class PaymentIntentOut(BaseModel):
    """
    Validation class for the PaymentIntent information being sent to the frontend.
    """

    client_secret: str
    total_snapshot_cents: int

class ReceiptOut(BaseModel):
    """
    Validation class for the Receipt being sent to the frontend.
    """

    payment_status: str
    price_snapshot_cents: int
    fee_snapshot_cents: int
    total_snapshot_cents: int
    team_name: str | None
    contact_username: str | None
    contact_email: str
    player_info: list[dict]
    custom_fields: dict
    org_twitter: str | None