import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt
from app.core.limiter import limiter, MODERATE
from app.schemas.wallet import MoneyRequest
from app.services import wallet as svc

# The Bank: the signed in account's uSync balance and its history. Amounts in requests are
# dollars; everything coming back is in cents (camelCase).
router = APIRouter(prefix="/wallet", tags=["Wallet"])

@router.get("")
async def getWallet(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.get_wallet(claims["sub"], db)

@router.get("/transactions")
async def getTransactions(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.list_transactions(claims["sub"], db)

@router.post("/deposits")
@limiter.limit(MODERATE)
async def deposit(request: Request, response: Response, body: MoneyRequest, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.deposit(claims["sub"], body.amount, db)

@router.post("/withdrawals")
@limiter.limit(MODERATE)
async def withdraw(request: Request, response: Response, body: MoneyRequest, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.request_withdrawal(claims["sub"], body.amount, db)

@router.post("/withdrawals/{transaction_id}/cancel")
async def cancelWithdrawal(transaction_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.cancel_withdrawal(claims["sub"], transaction_id, db)
