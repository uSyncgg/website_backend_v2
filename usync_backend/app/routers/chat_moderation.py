import uuid

from typing import Literal
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, require_admin
from app.core.limiter import limiter, MODERATE
from app.schemas.chat import ChatReportResolve
from app.services import chat_moderation as svc

# uSync staff only (the admins table): the queue of reported match chat messages. Reading a chat
# here is audited like every other admin request.
router = APIRouter(prefix="/admin/chat-reports", tags=["Admin"])

@router.get("")
@limiter.limit(MODERATE)
async def listChatReports(request: Request, response: Response, status: Literal["open", "actioned", "dismissed", "all"] = Query(default = "open"), claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await svc.list_reports(status, db)

@router.get("/{report_id}/history")
@limiter.limit(MODERATE)
async def chatReportHistory(request: Request, response: Response, report_id: uuid.UUID, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await svc.report_history(report_id, db)

@router.post("/{report_id}/resolve")
@limiter.limit(MODERATE)
async def resolveChatReport(request: Request, response: Response, report_id: uuid.UUID, body: ChatReportResolve, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await svc.resolve_report(report_id, body.action, body.note, claims, db)
