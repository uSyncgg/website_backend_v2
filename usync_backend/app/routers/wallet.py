from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from app.core.dependencies import get_db, get_stripe_client, verify_supabase_jwt
from app.core.limiter import limiter, MODERATE, STRICT_PAYMENT
from app.schemas.wallet import WalletOut, WalletHistoryOut, DepositIn, DepositStartOut
from app.services import wallet as svc

# The signed in account's own wallet. Nobody else's is ever readable here.
router = APIRouter(prefix="/wallet", tags=["Wallet"])

@router.get("", response_model = WalletOut)
@limiter.limit(MODERATE)
async def myWallet(request: Request, response: Response, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.my_wallet(claims["sub"], db)

@router.get("/history", response_model = WalletHistoryOut)
@limiter.limit(MODERATE)
async def walletHistory(request: Request, response: Response, before: int | None = Query(default = None),
                        claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.history(claims["sub"], db, before)

@router.post("/deposits", response_model = DepositStartOut)
@limiter.limit(STRICT_PAYMENT)
async def startDeposit(request: Request, response: Response, body: DepositIn, claims: dict = Depends(verify_supabase_jwt),
                       db: AsyncSession = Depends(get_db), stripeClient: StripeClient = Depends(get_stripe_client)):
    """
    Starts a deposit: returns the PaymentIntent's client secret for the Payment Element. The
    wallet is credited by Stripe's webhook, not by this call.
    """

    return await svc.start_deposit(claims["sub"], body.amount_cents, db, stripeClient)
