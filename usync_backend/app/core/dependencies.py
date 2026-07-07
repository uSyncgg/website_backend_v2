from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from fastapi import Request
from .db import AsyncSessionLocal

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

def get_stripe_client(request: Request) -> StripeClient:
    return request.app.state.stripe
