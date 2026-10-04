from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.settings import settings

# Tests run each test in its own event loop, so they must not reuse pooled connections.
engine_options = {"poolclass": NullPool} if settings.TESTING else {"pool_pre_ping": True}
engine = create_async_engine(settings.DATABASE_URL, **engine_options)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
