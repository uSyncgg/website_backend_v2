from fastapi import FastAPI
from app.core.db import engine
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Creating functionality for the lifespan of the backend application in order to know when
    to spin down resources and what to do upon startup.

    ::param app the FastAPI application.
    """

    yield

    await engine.dispose()

app = FastAPI(lifespan=lifespan)

