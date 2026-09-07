import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.db import engine, AsyncSessionLocal
from app.core.middleware import add_process_time
from contextlib import asynccontextmanager
from stripe import StripeClient
from dotenv import load_dotenv
from cachetools import TTLCache

from app.routers import tournaments
from app.routers import healthcheck
from app.routers import events
from app.routers import sitemap

from app.services import GAMES
from app.services.sitemap import get_sitemap_xml
from app.services.events import populate_verified_events_cache

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Creating functionality for the lifespan of the backend application in order to know when
    to spin down resources and what to do upon startup.

    ::param app the FastAPI application.
    """

    # app.state.stripe = StripeClient(os.getenv("STRIPE_TEST_KEY"))
    app.state.sitemap_cache = TTLCache(maxsize=1, ttl=3600)
    app.state.verified_cache = TTLCache(maxsize=len(GAMES), ttl=3600)
    async with AsyncSessionLocal() as db:
        await get_sitemap_xml(db, app.state.sitemap_cache)
        await populate_verified_events_cache(db, app.state.verified_cache)

    yield

    await engine.dispose()

app = FastAPI(lifespan=lifespan)

app.middleware("http")(add_process_time)

app.add_middleware(
    CORSMiddleware,
    allow_origins = [
        "https://www.usync.gg",
        "https://usync.gg"
    ],
    allow_origin_regex = r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"]
)

app.include_router(tournaments.router)
app.include_router(healthcheck.router)
app.include_router(events.router)
app.include_router(sitemap.router)
