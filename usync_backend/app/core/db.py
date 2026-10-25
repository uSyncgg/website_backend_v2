import os
from uuid import uuid4

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from dotenv import load_dotenv

load_dotenv()

# Runs through Supabase's transaction-mode pooler (port 6543), so prepared statements can't be
# cached or reused by name across transactions: disable the cache and give each one a unique name.
engine = create_async_engine(
    os.getenv("ASYNC_SUPABASE_CONNECTION_URL"),
    pool_size=5,
    max_overflow=5,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    },
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
