import os
import secrets

from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from fastapi import Request, Header, HTTPException, status
from .db import AsyncSessionLocal
from cachetools import TTLCache

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    
    """

    async with AsyncSessionLocal() as session:
        yield session

def get_stripe_client(request: Request) -> StripeClient:
    """
    
    """

    return request.app.state.stripe

def get_sitemap_cache(request: Request) -> TTLCache:
    """
    
    """

    return request.app.state.sitemap_cache

def get_verified_cache(request: Request) -> TTLCache:
    """
    
    """

    return request.app.state.verified_cache

def verify_sitemap_token(x_sitemap_token: str = Header(...)) -> None:
    if not secrets.compare_digest(x_sitemap_token, os.getenv("SITEMAP_INVALIDATION_TOKEN")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
