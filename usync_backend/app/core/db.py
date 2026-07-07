import os

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from dotenv import load_dotenv

load_dotenv()

engine = create_async_engine(os.getenv("ASYNC_SUPABASE_CONNECTION_URL"), pool_size=10, max_overflow=20, connect_args={"statement_cache_size": 0})
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
