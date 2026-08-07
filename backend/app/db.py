from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine_kwargs: dict = {"echo": False}
if settings.is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
elif "postgresql" in settings.database_url or "postgres" in settings.database_url:
    # Fail faster on Railway when Postgres isn't ready yet (startup retries handle it)
    engine_kwargs["connect_args"] = {"timeout": 10}
    engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(settings.database_url, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
