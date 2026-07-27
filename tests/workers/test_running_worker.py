import pytest
from decimal import Decimal
from unittest.mock import AsyncMock

from app.api.provider.provider_client import ProviderPayment
from app.db.models.enums import OperationStatus, SendJobState
from conftest import TestSessionLocal
from app.db.models import SendJob, Operation



@pytest.mark.asyncio
async def test_running_worker_completes_successful_payment(
    running_operation,
    provider_client,
    running_worker,
):
    # Arrange
    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id="provider-payment-123",
            status="ACCEPTED",
        ),
    )

    # Act
    await running_worker._process_job(
        running_operation,
    )

    # Assert
    provider_client.create_payment.assert_awaited_once_with(
        operation_id=running_operation,
        amount=Decimal("1000.00"),
        currency="RUB",
    )

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        # HTTP 202 from provider does not prove
        # that the payment was completed.
        assert operation.status == OperationStatus.PROCESSING

        # Provider payment ID is stored for future callbacks.
        assert operation.provider_payment_id == "provider-payment-123"

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_running_worker_moves_job_to_retry_on_provider_error(
    running_operation,
    provider_client,
    running_worker,
):
    # Arrange
    provider_client.create_payment = AsyncMock(
        side_effect=RuntimeError(
            "Provider unavailable",
        ),
    )

    # Act
    await running_worker._process_job(
        running_operation,
    )

    # Assert
    provider_client.create_payment.assert_awaited_once_with(
        operation_id=running_operation,
        amount=Decimal("1000.00"),
        currency="RUB",
    )

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        # A transport/provider error does not prove
        # that the payment was rejected.
        assert operation.status == OperationStatus.PROCESSING

        # We have no provider ID because the HTTP call
        # did not return a successful response.
        assert operation.provider_payment_id is None

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.WAITING_RETRY
        assert send_job.attempt == 1
        assert send_job.last_error == "Provider unavailable"


@pytest.mark.asyncio
async def test_running_worker_does_not_rollback_completed_operation(
    client,
    running_operation,
    provider_client,
    running_worker,
):
    # Arrange
    provider_payment_id = "provider-payment-123"

    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id=provider_payment_id,
            status="ACCEPTED",
        ),
    )


    # Simulate a callback that arrived before the HTTP 202
    # response from the provider.
    receipt_response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": provider_payment_id,
            "operationId": running_operation,
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-07-25T12:00:00Z",
        },
    )

    assert receipt_response.status_code == 204

    # Act
    #
    # The provider HTTP response arrives late.
    # RunningWorker must not rollback COMPLETED -> PROCESSING.
    await running_worker._process_job(
        running_operation,
    )

    # Assert

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        # Callback is the only source of final payment status.
        assert operation.status == OperationStatus.COMPLETED

        assert operation.provider_payment_id == provider_payment_id

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None

        # Callback should have finalized the delivery job.
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_running_worker_skips_job_that_is_no_longer_running(
    running_operation,
    provider_client,
    running_worker,
    send_job_service,
):
    # Arrange
    #
    # running_operation fixture guarantees:
    #
    # Operation = PROCESSING
    # SendJob = RUNNING
    #
    # Simulate another worker/process changing the job state
    # before RunningWorker starts processing it.
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Another worker claimed the job",
        )

        await session.commit()

    provider_client.create_payment = AsyncMock()

    # Act
    await running_worker._process_job(
        running_operation,
    )

    # Assert
    # RunningWorker must not send a payment request
    # for a job that is no longer RUNNING.
    provider_client.create_payment.assert_not_awaited()

    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.WAITING_RETRY


@pytest.mark.asyncio
async def test_receipt_rejects_provider_payment_id_conflict(
    client,
    running_operation,
):
    # Arrange
    existing_provider_payment_id = "provider-payment-existing"
    conflicting_provider_payment_id = "provider-payment-conflict"

    # First receipt establishes the provider payment identity.
    first_receipt_response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": existing_provider_payment_id,
            "operationId": running_operation,
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-07-25T12:00:00Z",
        },
    )

    assert first_receipt_response.status_code == 204

    # Act
    # A receipt for the same operation with a different
    # providerPaymentId must be rejected.
    conflicting_receipt_response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": conflicting_provider_payment_id,
            "operationId": running_operation,
            "result": "COMPLETED",
            "message": "Payment completed again",
            "occurredAt": "2026-07-25T12:00:01Z",
        },
    )

    # Assert
    assert conflicting_receipt_response.status_code == 409

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        # The original providerPaymentId must never be overwritten.
        assert (
            operation.provider_payment_id
            == existing_provider_payment_id
        )

        # The operation remains in its original final state.
        assert operation.status == OperationStatus.COMPLETED

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE