import httpx
import pytest
import json

from decimal import Decimal

from app.api.provider.provider_client import ProviderClient
from conftest import TestSessionLocal
from app.db.models import SendJob, Operation
from app.db.models.enums import SendJobState, OperationStatus
from app.workers.retry_worker import RetryWorker
from app.workers.running_worker import RunningWorker


@pytest.mark.asyncio
async def test_provider_client_sends_idempotency_and_correlation_headers():
    # Arrange
    operation_id = "operation-123"

    captured_request: httpx.Request | None = None

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal captured_request

        captured_request = request

        return httpx.Response(
            status_code=202,
            json={
                "providerPaymentId": "provider-payment-123",
                "status": "ACCEPTED",
            },
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://provider",
    ) as client:
        provider_client = ProviderClient(
            client=client,
            provider_url="http://provider",
        )

        # Act
        result = await provider_client.create_payment(
            operation_id=operation_id,
            amount=Decimal("1000.00"),
            currency="RUB",
        )

    # Assert
    assert result.provider_payment_id == (
        "provider-payment-123"
    )

    assert result.status == "ACCEPTED"

    assert captured_request is not None

    # Idempotency-Key must be exactly operation_id.
    assert (
        captured_request.headers["Idempotency-Key"]
        == operation_id
    )

    # X-Correlation-ID must also be exactly operation_id.
    assert (
        captured_request.headers["X-Correlation-ID"]
        == operation_id
    )

    # Both headers must be identical.
    assert (
        captured_request.headers["Idempotency-Key"]
        == captured_request.headers["X-Correlation-ID"]
    )

    # Verify the request body.
    assert json.loads(captured_request.content) == {
        "operationId": operation_id,
        "amount": "1000.00",
        "currency": "RUB",
    }




@pytest.mark.asyncio
async def test_provider_client_uses_same_idempotency_key_and_correlation_id_on_retries():
    # Arrange
    operation_id = "operation-123"

    requests: list[httpx.Request] = []

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        requests.append(request)

        if len(requests) == 1:
            # Simulate a temporary provider/network failure.
            return httpx.Response(
                status_code=503,
                json={
                    "detail": "Provider unavailable",
                },
            )

        return httpx.Response(
            status_code=202,
            json={
                "providerPaymentId": "provider-payment-123",
                "status": "ACCEPTED",
            },
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://provider",
    ) as client:
        provider_client = ProviderClient(
            client=client,
            provider_url="http://provider",
        )

        # Act
        with pytest.raises(httpx.HTTPStatusError):
            await provider_client.create_payment(
                operation_id=operation_id,
                amount=Decimal("1000.00"),
                currency="RUB",
            )

        result = await provider_client.create_payment(
            operation_id=operation_id,
            amount=Decimal("1000.00"),
            currency="RUB",
        )

    # Assert
    assert result.provider_payment_id == (
        "provider-payment-123"
    )

    assert len(requests) == 2

    first_request = requests[0]
    second_request = requests[1]

    # Both attempts must use the same Idempotency-Key.
    assert (
        first_request.headers["Idempotency-Key"]
        == operation_id
    )

    assert (
        second_request.headers["Idempotency-Key"]
        == operation_id
    )

    assert (
        first_request.headers["Idempotency-Key"]
        == second_request.headers["Idempotency-Key"]
    )

    # Both attempts must use the same X-Correlation-ID.
    assert (
        first_request.headers["X-Correlation-ID"]
        == operation_id
    )

    assert (
        second_request.headers["X-Correlation-ID"]
        == operation_id
    )

    assert (
        first_request.headers["X-Correlation-ID"]
        == second_request.headers["X-Correlation-ID"]
    )

    # The request bodies must also remain identical.
    assert json.loads(first_request.content) == json.loads(second_request.content)


@pytest.mark.asyncio
async def test_retry_reuses_same_idempotency_key_and_correlation_id(
    running_operation,
    send_job_service,
    real_provider_client,
    provider_requests,
):
    # Arrange
    running_worker = RunningWorker(
        session_factory=TestSessionLocal,
        send_job_service=send_job_service,
        provider_client=real_provider_client,
        poll_interval=0.01,
    )

    retry_worker = RetryWorker(
        session_factory=TestSessionLocal,
        send_job_service=send_job_service,
        provider_client=real_provider_client,
        poll_interval=0.01,
    )

    # Act
    # First attempt fails with a network error.
    await running_worker._process_job(
        running_operation,
    )

    # Verify that the job entered the retry state.
    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.WAITING_RETRY

    # Retry attempt succeeds.
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    assert len(provider_requests) == 2

    first_request = provider_requests[0]
    retry_request = provider_requests[1]

    # ---------------------------------------------------------
    # Idempotency-Key
    # ---------------------------------------------------------

    assert (
        first_request.headers["Idempotency-Key"]
        == running_operation
    )

    assert (
        retry_request.headers["Idempotency-Key"]
        == running_operation
    )

    assert (
        first_request.headers["Idempotency-Key"]
        == retry_request.headers["Idempotency-Key"]
    )

    # ---------------------------------------------------------
    # X-Correlation-ID
    # ---------------------------------------------------------

    assert (
        first_request.headers["X-Correlation-ID"]
        == running_operation
    )

    assert (
        retry_request.headers["X-Correlation-ID"]
        == running_operation
    )

    assert (
        first_request.headers["X-Correlation-ID"]
        == retry_request.headers["X-Correlation-ID"]
    )

    # ---------------------------------------------------------
    # Request body
    # ---------------------------------------------------------

    assert json.loads(first_request.content) == {
        "operationId": running_operation,
        "amount": "1000.00",
        "currency": "RUB",
    }

    assert json.loads(retry_request.content) == {
        "operationId": running_operation,
        "amount": "1000.00",
        "currency": "RUB",
    }

    assert (
        json.loads(first_request.content)
        == json.loads(retry_request.content)
    )

    # ---------------------------------------------------------
    # Final local state
    # ---------------------------------------------------------

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        assert (
            operation.provider_payment_id
            == "provider-payment-retry"
        )

        # Provider 202 is not a final payment result.
        assert operation.status == OperationStatus.PROCESSING

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE