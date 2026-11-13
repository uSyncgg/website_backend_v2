import uuid

from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.admin import AdminAuditLog
from app.models.chat import ChatReports, MatchChatMessages, MatchChatAdmins

# What uSync staff do with reported chat messages (the admin dashboard's Moderation tab). Reports
# come from players, from admins who joined a chat, and automatically when an admin hides a
# message. They never reach an event's own admins. Every request here is already written to
# admin_audit_log by require_admin; resolving also records what was decided.

LIST_LIMIT = 200
HISTORY_LIMIT = 1000

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None

def _report_out(r: ChatReports) -> dict:
    return {
        "id": str(r.id),
        "source": r.source,
        "category": r.category,
        "note": r.note,
        "status": r.status,
        "created_at": _iso(r.created_at),
        "reporter_name": r.reporter_name,
        "sender_name": r.sender_name,
        "sender_role": r.sender_role,
        "sender_account_id": str(r.sender_account_id) if r.sender_account_id else None,
        "message": r.message_body,
        "event_id": r.event_public_id,
        "event_name": r.event_name,
        "match_id": r.match_id,
        "chat_available": r.room_id is not None,
        "reviewed_by": r.reviewed_by_email,
        "reviewed_at": _iso(r.reviewed_at),
        "resolution_note": r.resolution_note,
    }

async def list_reports(status: str, db: AsyncSession) -> dict:
    """
    Reports newest first (open ones by default), with how many are open overall for the tab badge.
    """

    query = select(ChatReports).order_by(ChatReports.created_at.desc()).limit(LIST_LIMIT)
    if status != "all":
        query = query.where(ChatReports.status == status)
    rows = (await db.execute(query)).scalars().all()
    open_count = (await db.execute(select(func.count()).select_from(ChatReports).where(ChatReports.status == "open"))).scalar()
    return {"open_count": open_count, "reports": [_report_out(r) for r in rows]}

async def _report(report_id: uuid.UUID, db: AsyncSession) -> ChatReports:
    report = await db.get(ChatReports, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    return report

async def report_history(report_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    The chat the report came from. While the chat still exists that's the whole live conversation;
    after it has been deleted it's the saved copy of the reported message and the lines around it.
    """

    report = await _report(report_id, db)
    base = {"report_id": str(report.id), "event_id": report.event_public_id, "event_name": report.event_name, "match_id": report.match_id, "highlight_id": report.message_id}

    if report.room_id:
        rows = (await db.execute(
            select(MatchChatMessages).where(MatchChatMessages.room_id == report.room_id).order_by(MatchChatMessages.id).limit(HISTORY_LIMIT)
        )).scalars().all()
        admins = (await db.execute(select(MatchChatAdmins).where(MatchChatAdmins.room_id == report.room_id))).scalars().all()
        return {
            **base, "live": True, "admins": [a.name for a in admins],
            "messages": [
                {"id": m.id, "kind": m.kind, "name": m.sender_name, "role": m.sender_role, "body": m.body, "at": _iso(m.created_at), "hidden": m.hidden_at is not None, "hidden_by": m.hidden_by_name}
                for m in rows
            ],
        }

    return {**base, "live": False, "admins": [], "messages": list(report.context or [])}

async def resolve_report(report_id: uuid.UUID, action: str, note: str | None, claims: dict, db: AsyncSession) -> dict:
    """
    Closes a report as actioned (staff acted on it elsewhere) or dismissed. Dismissing a report that
    came from an admin hiding a message puts that message back for the players.
    """

    report = await _report(report_id, db)
    if report.status != "open":
        raise HTTPException(status_code=409, detail="That report was already reviewed.")

    email = claims.get("email") or "unknown"
    report.status = action
    report.reviewed_by_email = email
    report.reviewed_at = datetime.now(timezone.utc)
    report.resolution_note = (note or "").strip() or None

    restored = False
    if action == "dismissed" and report.source == "admin_hide" and report.message_id:
        message = await db.get(MatchChatMessages, report.message_id)
        if message and message.hidden_at is not None:
            message.hidden_at = None
            message.hidden_by_account_id = None
            message.hidden_by_name = None
            restored = True

    db.add(AdminAuditLog(
        admin_id = uuid.UUID(claims["sub"]), admin_email = email, action = f"chat_report:{action}",
        details = {"report_id": str(report.id), "event_id": report.event_public_id, "match_id": report.match_id, "restored_message": restored},
    ))
    await db.commit()
    return _report_out(report)
