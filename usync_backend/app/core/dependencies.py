import os
import jwt
import secrets

from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from fastapi import Request, Header, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .db import AsyncSessionLocal
from cachetools import TTLCache

bearer_scheme = HTTPBearer()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Asyncronous local session that provides access to acquire the database object.
    """

    async with AsyncSessionLocal() as session:
        yield session

def get_stripe_client(request: Request) -> StripeClient:
    """
    Getter function to get the stripe client via a request.

    ::param request the Request object to access the stripe client

    ::return the Stripe Client 
    """

    return request.app.state.stripe

def get_sitemap_cache(request: Request) -> TTLCache:
    """
    Getter function to get the TTL sitemap cache via a request.

    ::param request the Request object to access the TTL sitemap cache

    ::return the TTL sitemap cache
    """

    return request.app.state.sitemap_cache

def get_verified_cache(request: Request) -> TTLCache:
    """
    Getter function to get the TTL verified events cache via a request.

    ::param request the Request object to access the TTL verified events cache

    ::return the TTL verified events cache
    """

    return request.app.state.verified_cache

def verify_sitemap_token(x_sitemap_token: str = Header(...)) -> None:
    """
    Verify the integrity of the sitemap token provided.

    ::param x_sitemap_token the sitemap token to use in comparison with the current sitemap token
    """

    if not secrets.compare_digest(x_sitemap_token, os.getenv("SITEMAP_INVALIDATION_TOKEN")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    return None

def verify_supabase_jwt(request: Request, creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """
    
    """

    try:
        signing_key = request.app.state.jwk_client.get_signing_key_from_jwt(creds.credentials)

        return jwt.decode(
            creds.credentials,
            signing_key.key,
            algorithms = ["ES256"],
            audience = "authenticated"
        )

    except jwt.PyJWTError:
        raise HTTPException(status_code = 401, detail = "Invalid or expired token")
