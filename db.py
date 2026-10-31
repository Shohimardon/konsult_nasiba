from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Base

engine = create_async_engine(settings.async_database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы. Для прод-масштаба — заменить на alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
