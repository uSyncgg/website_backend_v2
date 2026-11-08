import re
import uuid

from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

ClaimRole = Literal["owner", "organizer", "staff", "other"]
ContactMethod = Literal["email", "discord", "twitter"]
ClaimStatus = Literal["pending", "approved", "rejected"]

MIN_DESCRIPTION = 30
MAX_DESCRIPTION = 2000

# Same rules as the frontend form; these are the ones that count.
CONTACT_PATTERNS = {
    "email": re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$"),
    "discord": re.compile(r"^\S{2,32}$"),
    "twitter": re.compile(r"^@?\w{1,15}$"),
}

class EventClaimIn(BaseModel):
    """
    The form fields of POST /host/event-claims (the proof image is a separate file field).
    The event is named only by its id: the backend looks up its name and type itself.
    """

    event_id: uuid.UUID
    role: ClaimRole
    description: Annotated[str, StringConstraints(strip_whitespace = True, min_length = MIN_DESCRIPTION, max_length = MAX_DESCRIPTION)]
    contact_method: ContactMethod
    contact: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 254)]

    @model_validator(mode = "after")
    def check_contact(self):
        if not CONTACT_PATTERNS[self.contact_method].match(self.contact):
            raise ValueError(f"That doesn't look like a valid {self.contact_method} contact.")

        return self

class ClaimTargetOut(BaseModel):
    """
    GET /host/event-claims/events/{event_id}: what the claim page needs to show before the form.
    `owned` means another account already owns it; `pending_claim` means this account already has a
    claim under review. Either one means the form shouldn't be shown.
    """

    event_id: uuid.UUID
    name: str
    event_type: str
    owned: bool
    pending_claim: bool

class EventClaimOut(BaseModel):
    """
    The created claim. Contact details and the proof path are never echoed back.
    """
    model_config = ConfigDict(from_attributes = True)

    id: uuid.UUID
    event_id: uuid.UUID
    status: ClaimStatus
    created_at: datetime
