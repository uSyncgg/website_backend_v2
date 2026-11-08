from pydantic import BaseModel, Field, field_validator
from app.services.chat_filters import MAX_MESSAGE_LENGTH

class ChatMessageIn(BaseModel):
    body: str = Field(min_length = 1, max_length = MAX_MESSAGE_LENGTH)

    @field_validator("body")
    @classmethod
    def tidy(cls, v: str) -> str:
        # One line of plain text: control characters out, runs of whitespace (newlines too) to one space.
        v = "".join(ch for ch in v if ch == "\n" or ch == "\t" or ch.isprintable())
        v = " ".join(v.split())
        if not v:
            raise ValueError("Type a message first.")
        return v
