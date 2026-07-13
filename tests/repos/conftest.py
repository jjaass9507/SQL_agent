"""tests/repos 共用 fixtures：每個測試用獨立的 SQLite in-memory 資料庫。"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.repos.models import Base


@pytest.fixture
async def db_session():
    """建立一個乾淨的 in-memory 資料庫並提供一個 AsyncSession。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
