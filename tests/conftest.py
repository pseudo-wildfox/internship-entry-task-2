import uuid
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.database import get_db
from app.main import app
from core.settings import settings

# ============================================================
# Test database
# ============================================================




test_engine = create_async_engine(
    settings.TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ============================================================
# Database session override
# ============================================================

async def override_get_session():
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_session


# ============================================================
# HTTP client
# ============================================================

@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


# ============================================================
# Database cleanup
# ============================================================

@pytest_asyncio.fixture
async def db_session():
    async with TestSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def clean_database():

    async with test_engine.begin() as connection:
        await connection.execute(
            text(
                """
                TRUNCATE TABLE
                    events,
                    send_jobs,
                    operations
                RESTART IDENTITY
                CASCADE
                """
            )
        )

    yield


# ============================================================
# Cleanup
# ============================================================

@pytest_asyncio.fixture(scope="session", autouse=True)
async def dispose_test_engine():
    yield

    await test_engine.dispose()


# ============================================================
# Operation pre-creation
# ============================================================


@pytest_asyncio.fixture
async def operation_id(client) -> str:
    operation_id = f"test-{uuid.uuid4()}"

    response = await client.post(
        "/operations",
        json={
            "operationId": operation_id,
            "amount": "1000.00",
            "currency": "RUB",
            "description": "Test payment",
        },
    )

    assert response.status_code == 201

    return operation_id