from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import settings


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def check_connection() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def get_db():
    async with SessionLocal() as session:
        yield session
