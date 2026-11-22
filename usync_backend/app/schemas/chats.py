from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# Request and response bodies for /chats (and the match chat routes under /brackets). camelCase
# like the bracket API. Response models list every field the API may send.

class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator = to_camel, populate_by_name = True, extra = "ignore")

class Out(BaseModel):
    model_config = ConfigDict(extra = "ignore")

class ChatMessageIn(CamelModel):
    body: str = Field(min_length = 1, max_length = 500)

class ChatReadIn(CamelModel):
    last_message_id: int = Field(ge = 0)

class ChatMessageOut(Out):
    id: int
    senderProfileId: str | None = None
    senderName: str | None = None
    senderRole: str
    body: str
    createdAt: str | None = None

class ChatOut(Out):
    id: str
    kind: str
    role: str
    title: str
    subtitle: str | None = None
    eventPublicId: str | None = None
    matchCode: str | None = None
    hasUnread: bool
    lastMessageAt: str | None = None
    closesAt: str | None = None
    openedAt: str | None = None
