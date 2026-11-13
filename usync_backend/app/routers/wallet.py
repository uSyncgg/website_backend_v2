import uuid

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt
from app.core.limiter import limiter, MODERATE
from app.schemas.wallet import MoneyRequest
from app.services import wallet as svc
from app.services.brackets import settle_payments_for_account

# The Bank: the signed in account's uSync balance and its history. Amounts in requests are
# dollars; everything coming back is in cents (camelCase).
router = APIRouter(prefix="/wallet", tags=["Wallet"])

def _csv(text: str, filename: str) -> Response:
    return Response(content = text, media_type = "text/csv", headers = {"Content-Disposition": f'attachment; filename="{filename}"'})

@router.get("")
async def getWallet(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.get_wallet(claims["sub"], db)

@router.get("/transactions")
async def getTransactions(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.list_transactions(claims["sub"], db)

@router.get("/transactions/{transaction_id}/receipt")
async def getReceipt(transaction_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.get_receipt(claims["sub"], transaction_id, db)

@router.get("/export")
@limiter.limit(MODERATE)
async def exportHistory(
    request: Request, response: Response,
    year: int | None = Query(default = None, ge = 2020, le = 2100),
    kind: str = Query(default = "transactions", pattern = "^(transactions|tax)$"),
    claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db),
):
    """
    The history as a CSV download: every transaction (optionally one year), or kind=tax for a
    one-year summary of winnings, earnings and fees.
    """

    if kind == "tax":
        year = year or datetime.now(timezone.utc).year
        return _csv(await svc.tax_summary_csv(claims["sub"], year, db), f"usync-tax-summary-{year}.csv")
    return _csv(await svc.export_csv(claims["sub"], year, db), f"usync-bank-history-{year or 'all'}.csv")

@router.get("/tax-summary")
async def getTaxSummary(year: int = Query(ge = 2020, le = 2100), claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.tax_summary(claims["sub"], year, db)

@router.get("/years")
async def getYears(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.available_years(claims["sub"], db)

@router.get("/stats")
async def getStats(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.get_stats(claims["sub"], db)

@router.post("/deposits")
@limiter.limit(MODERATE)
async def deposit(request: Request, response: Response, body: MoneyRequest, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await svc.deposit(claims["sub"], body.amount, db)
    # New money: any team entry that was waiting on this player's share takes it now.
    await settle_payments_for_account(claims["sub"], db)
    return await svc.get_wallet(claims["sub"], db)

@router.post("/withdrawals")
@limiter.limit(MODERATE)
async def withdraw(request: Request, response: Response, body: MoneyRequest, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.request_withdrawal(claims["sub"], body.amount, db)

@router.post("/withdrawals/{transaction_id}/cancel")
async def cancelWithdrawal(transaction_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await svc.cancel_withdrawal(claims["sub"], transaction_id, db)
    await settle_payments_for_account(claims["sub"], db)
    return await svc.get_wallet(claims["sub"], db)
