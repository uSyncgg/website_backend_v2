import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt
from app.schemas.chats import ChatMessageIn, ChatReadIn, ChatMessageOut, ChatOut
from app.services import chats as svc

# Chats the signed-in account is a member of. New messages arrive over Supabase Realtime
# Broadcast (channel chat:{id}); these routes are for sending, the list, and catching up on
# anything missed (?after=<last message id seen>). Per-chat send limits are in the service.
router = APIRouter(prefix="/chats", tags=["Chats"])

@router.get("", response_model = list[ChatOut])
async def getMyChats(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.my_chats(claims["sub"], db)

@router.get("/{chat_id}/messages", response_model = list[ChatMessageOut])
async def getMessages(chat_id: uuid.UUID, after: int = Query(0, ge = 0), claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.messages(claims["sub"], chat_id, after, db)

@router.post("/{chat_id}/messages", response_model = ChatMessageOut)
async def sendMessage(chat_id: uuid.UUID, body: ChatMessageIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.send(claims["sub"], chat_id, body.body, db)

@router.post("/{chat_id}/read", status_code = 204)
async def markRead(chat_id: uuid.UUID, body: ChatReadIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await svc.mark_read(claims["sub"], chat_id, body.last_message_id, db)
