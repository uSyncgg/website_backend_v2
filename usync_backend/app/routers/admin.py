from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from stripe import StripeClient
from app.core.dependencies import get_db, get_readonly_db, get_stripe_client, require_admin, require_money_admin
from app.core.limiter import limiter, MODERATE
from app.schemas.admin import AdminAccountsOut, AdminAnalyticsOut, AdminEventsOut
from app.services.admin import get_admin_accounts, get_admin_analytics, get_admin_events
from app.services.brackets import admin_reset_bracket
from app.schemas.wallet import (
    AdminWalletOut, ManualMoveIn, DepositRefundIn, EventCancelIn, AdminWithdrawalOut, AdminWithdrawalsOut,
    WithdrawalCompleteIn, WithdrawalReturnIn,
)
from app.services import wallet as wallet_svc
from app.services import withdrawals as withdrawals_svc

# Admin dashboard, admins only (the admins table; every allowed request is in admin_audit_log).
# Reads use a READ ONLY session; the few support actions use a normal one.
router = APIRouter(prefix="/admin", tags=["Admin"])

@router.get("/access", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(MODERATE)
async def adminAccess(request: Request, response: Response, claims: dict = Depends(require_admin)) -> None:
    """
    204 for admins, 403 for everyone else. The frontend calls this before showing the dashboard.
    """

@router.get("/analytics", response_model=AdminAnalyticsOut)
@limiter.limit(MODERATE)
async def adminAnalytics(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Account totals, daily sign-ups, and the games listed on profiles.
    """

    return await get_admin_analytics(db)

@router.get("/events", response_model=AdminEventsOut)
@limiter.limit(MODERATE)
async def adminEvents(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Every event listing, most recently updated first.
    """

    return await get_admin_events(db)

@router.get("/accounts", response_model=AdminAccountsOut)
@limiter.limit(MODERATE)
async def adminAccounts(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Every account, including unfinished sign-ups, with roles, games and linked platforms.
    """

    return await get_admin_accounts(db)

@router.delete("/events/{event_id}/bracket", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(MODERATE)
async def adminResetBracket(request: Request, response: Response, event_id: str, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> None:
    """
    Support: throws a published bracket away so the host can reseed it, until any match has a
    result. Hosts can only reset a draft.
    """

    await admin_reset_bracket(event_id, db)

# ---- wallet tools (admins with can_manage_money) ----------------------------------------

@router.post("/events/{event_id}/cancel")
@limiter.limit(MODERATE)
async def adminCancelEvent(request: Request, response: Response, event_id: str, body: EventCancelIn,
                           claims: dict = Depends(require_money_admin), db: AsyncSession = Depends(get_db)) -> dict:
    """
    Cancels an event and refunds every entry fee and the guaranteed prize. Audit logged.
    """

    return await wallet_svc.admin_cancel_event(claims, event_id, body.reason, db)

@router.get("/wallets", response_model=AdminWalletOut)
@limiter.limit(MODERATE)
async def adminWallet(request: Request, response: Response, email: str, claims: dict = Depends(require_money_admin), db: AsyncSession = Depends(get_db)):
    """
    One account's wallet (by email) and its last 20 deposits.
    """

    return await wallet_svc.admin_wallet(email, db)

@router.post("/wallets/adjustments", response_model=AdminWalletOut)
@limiter.limit(MODERATE)
async def adminAdjustWallet(request: Request, response: Response, body: ManualMoveIn, claims: dict = Depends(require_money_admin), db: AsyncSession = Depends(get_db)):
    """
    A manual credit or debit of a wallet's Deposited Funds (e.g. a wired guaranteed prize), with
    a reason and a reference. Audit logged and emailed to MONEY_ALERT_EMAIL.
    """

    return await wallet_svc.manual_move(claims, body.email, body.direction, body.amount_cents, body.reason, body.reference, body.idempotency_key, db)

@router.post("/deposits/{deposit_id}/refund", response_model=AdminWalletOut)
@limiter.limit(MODERATE)
async def adminRefundDeposit(request: Request, response: Response, deposit_id: uuid.UUID, body: DepositRefundIn,
                             claims: dict = Depends(require_money_admin), db: AsyncSession = Depends(get_db),
                             stripeClient: StripeClient = Depends(get_stripe_client)):
    """
    Refunds a deposit (amount + fee) to its card, if that much is still in Deposited Funds.
    """

    return await wallet_svc.refund_deposit(claims, deposit_id, body.reason, db, stripeClient)

@router.get("/withdrawals", response_model=AdminWithdrawalsOut)
@limiter.limit(MODERATE)
async def adminWithdrawals(request: Request, response: Response,
                           status_: str = Query(default = "pending", alias = "status", pattern = "^(pending|completed|returned)$"),
                           claims: dict = Depends(require_money_admin), db: AsyncSession = Depends(get_db)):
    """
    Withdrawals with this status, oldest first, with each wallet's current Earnings and frozen state.
    """

    return await withdrawals_svc.list_withdrawals(status_, db)

@router.post("/withdrawals/{withdrawal_id}/complete", response_model=AdminWithdrawalOut)
@limiter.limit(MODERATE)
async def adminCompleteWithdrawal(request: Request, response: Response, withdrawal_id: uuid.UUID, body: WithdrawalCompleteIn,
                                  claims: dict = Depends(require_money_admin), db: AsyncSession = Depends(get_db)):
    """
    The money was sent by hand: it leaves the wallet for good. Refused while the wallet is frozen. Audit logged.
    """

    return await withdrawals_svc.complete_withdrawal(claims, withdrawal_id, body.external_reference, db)

@router.post("/withdrawals/{withdrawal_id}/return", response_model=AdminWithdrawalOut)
@limiter.limit(MODERATE)
async def adminReturnWithdrawal(request: Request, response: Response, withdrawal_id: uuid.UUID, body: WithdrawalReturnIn,
                                claims: dict = Depends(require_money_admin), db: AsyncSession = Depends(get_db)):
    """
    Not sent: the money goes back to the user's Earnings, with a reason they see. Audit logged.
    """

    return await withdrawals_svc.return_withdrawal(claims, withdrawal_id, body.reason, db)
