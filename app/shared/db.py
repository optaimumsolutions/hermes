import asyncpg
from contextlib import asynccontextmanager
from .config import get_settings

_pool: asyncpg.Pool | None = None
_lead_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            get_settings().database_url,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def get_lead_pool() -> asyncpg.Pool:
    """Pool for the upstream lead database (read-only)."""
    global _lead_pool
    if _lead_pool is None:
        s = get_settings()
        url = s.lead_database_url or s.database_url
        _lead_pool = await asyncpg.create_pool(
            url, min_size=1, max_size=5, command_timeout=30,
        )
    return _lead_pool


async def close_pool():
    global _pool, _lead_pool
    if _pool:
        await _pool.close()
        _pool = None
    if _lead_pool:
        await _lead_pool.close()
        _lead_pool = None


@asynccontextmanager
async def db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def lead_db():
    """Connection to the upstream lead database (read-only)."""
    pool = await get_lead_pool()
    async with pool.acquire() as conn:
        yield conn
