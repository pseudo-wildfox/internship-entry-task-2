import uuid
from unittest.mock import AsyncMock

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.provider.provider_client import ProviderClient
from app.db.database import get_db
from app.main import app
from app.core.test_settings import test_settings
from app.services.send_job_service import SendJobService
from app.workers.running_worker import RunningWorker
from app.workers.retry_worker import RetryWorker

# ============================================================
# Test database
# ============================================================




test_engine = create_async_engine(
    test_settings.TEST_DATABASE_URL,
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


@pytest_asyncio.fixture
async def send_job_service():
    return SendJobService.create_default()


@pytest_asyncio.fixture
async def provider_client():
    return AsyncMock()


@pytest_asyncio.fixture
async def running_worker(
    provider_client,
    send_job_service,
):
    return RunningWorker(
        session_factory=TestSessionLocal,
        send_job_service=send_job_service,
        provider_client=provider_client,
        poll_interval=0.01,
    )


@pytest_asyncio.fixture
async def running_operation(
    client,
    operation_id,
    send_job_service,
):
    """
    Creates an operation through the public API
    and prepares its SendJob for RunningWorker.

    State transition:

        CREATED
            │
            │ POST /submit
            ▼
        Operation = PROCESSING
        SendJob = PENDING
            │
            │ claim_send_job()
            ▼
        SendJob = RUNNING
    """

    response = await client.post(
        f"/operations/{operation_id}/submit",
    )

    assert response.status_code == 202

    async with TestSessionLocal() as session:
        claimed = await send_job_service.claim_send_job(
            session=session,
            operation_id=operation_id,
        )

        assert claimed is True

        await session.commit()

    return operation_id


@pytest_asyncio.fixture
async def retry_worker(
    provider_client,
    send_job_service,
):
    return RetryWorker(
        session_factory=TestSessionLocal,
        send_job_service=send_job_service,
        provider_client=provider_client,
        poll_interval=0.01,
    )


@pytest_asyncio.fixture
async def provider_requests():
    return []


@pytest_asyncio.fixture
async def real_provider_client(
    provider_requests,
):
    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        provider_requests.append(request)

        if len(provider_requests) == 1:
            # First attempt: simulate a network failure.
            raise httpx.ConnectError(
                "Connection reset",
                request=request,
            )

        # Retry: provider accepts the request.
        return httpx.Response(
            status_code=202,
            json={
                "providerPaymentId": "provider-payment-retry",
                "status": "ACCEPTED",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)

    client = httpx.AsyncClient(
        transport=transport,
        base_url="http://provider",
    )

    provider_client = ProviderClient(
        client=client,
        provider_url="http://provider",
    )

    yield provider_client

    await client.aclose()