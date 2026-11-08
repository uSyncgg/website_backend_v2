from fastapi import APIRouter, Depends, Query, Request, Response
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, get_openai, verify_supabase_jwt
from app.core.limiter import limiter, MODERATE, STRICT_CHAT_MESSAGE
from app.schemas.chat import ChatMessageIn
from app.services import chat as svc

# Match chat rooms. One room per match, reached from the match page; polled by the frontend
# with ?after=<last message id>. Players are everyone on either side; the host and event admins
# see nothing until they join.
router = APIRouter(prefix="/brackets/{event_id}/matches/{match_id}/chat", tags=["Match chat"])

@router.get("")
@limiter.limit(MODERATE)
async def getChat(request: Request, response: Response, event_id: str, match_id: str, after: int | None = Query(default = None, ge = 0), claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.get_chat(claims["sub"], event_id, match_id, after, db)

@router.post("/messages")
@limiter.limit(STRICT_CHAT_MESSAGE)
async def sendMessage(request: Request, response: Response, event_id: str, match_id: str, body: ChatMessageIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db), openai: AsyncOpenAI = Depends(get_openai)):
    return await svc.send_message(claims["sub"], event_id, match_id, body.body, openai, db)

@router.post("/request-admin")
@limiter.limit(MODERATE)
async def requestAdmin(request: Request, response: Response, event_id: str, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.request_admin(claims["sub"], event_id, match_id, db)

@router.post("/join")
@limiter.limit(MODERATE)
async def joinChat(request: Request, response: Response, event_id: str, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.join_chat(claims["sub"], event_id, match_id, db)
