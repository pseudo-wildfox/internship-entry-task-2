from datetime import timedelta, datetime, timezone

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock

from app.api.provider.provider_client import ProviderPayment
from app.db.models.enums import OperationStatus, SendJobState
from conftest import TestSessionLocal
from app.db.models import SendJob, Operation
from app.workers.retry_worker import RetryWorker
from workers.running_worker import RunningWorker


@pytest.mark.asyncio
async def test_retry_worker_completes_waiting_retry_job_on_success(
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    provider_payment_id = "provider-payment-retry-success"

    # First attempt failed and moved the job into WAITING_RETRY.
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Provider returned 503",
        )

        await session.commit()

    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id=provider_payment_id,
            status="ACCEPTED",
        ),
    )

    # Act
    await retry_worker._process_job(
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

        # The provider accepted the payment request,
        # but the final payment status is still determined
        # exclusively by the receipt callback.
        assert operation.status == OperationStatus.PROCESSING

        assert (
            operation.provider_payment_id
            == provider_payment_id
        )

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None

        assert send_job.state == SendJobState.DONE

        # Retry metadata is cleared after successful delivery.
        assert send_job.next_retry_at is None
        assert send_job.last_error is None

        # One failed attempt happened before the successful retry.
        assert send_job.attempt == 1



@pytest.mark.asyncio
async def test_retry_worker_keeps_job_in_waiting_retry_after_failed_retry(
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Initial provider failure",
        )

        await session.commit()

    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None

        initial_attempt = send_job.attempt
        initial_next_retry_at = send_job.next_retry_at

    provider_client.create_payment = AsyncMock(
        side_effect=RuntimeError(
            "Provider is still unavailable",
        ),
    )

    # Act
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    provider_client.create_payment.assert_awaited_once()

    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None

        # Retry failure must not move the job out of
        # the retry state.
        assert send_job.state == SendJobState.WAITING_RETRY

        # Failed retry increments the attempt counter.
        assert send_job.attempt == initial_attempt + 1

        # The latest error is stored.
        assert (
            send_job.last_error
            == "Provider is still unavailable"
        )

        # The retry policy schedules another attempt.
        assert send_job.next_retry_at is not None

        assert (
            send_job.next_retry_at
            != initial_next_retry_at
        )


@pytest.mark.asyncio
async def test_retry_worker_skips_job_that_is_no_longer_waiting_for_retry(
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Provider returned 503",
        )

        await session.commit()

    # Simulate another process completing the delivery job
    # before RetryWorker starts processing it.
    async with TestSessionLocal() as session:
        operation = await send_job_service.get_operation_for_update(
            session,
            running_operation,
        )

        assert operation.send_job is not None

        operation.send_job.state = SendJobState.DONE
        operation.send_job.next_retry_at = None
        operation.send_job.last_error = None

        await session.commit()

    provider_client.create_payment = AsyncMock()

    # Act
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    # RetryWorker must not call the provider for a job
    # that is no longer WAITING_RETRY.
    provider_client.create_payment.assert_not_awaited()

    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_retry_worker_does_not_rollback_completed_operation(
    client,
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    provider_payment_id = "provider-payment-retry-completed"

    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Provider returned 503",
        )

        await session.commit()

    # The provider callback arrives before RetryWorker retries.
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

    provider_client.create_payment = AsyncMock()

    # Act
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    # The callback has already finalized the payment.
    # RetryWorker must not send another provider request.
    provider_client.create_payment.assert_not_awaited()

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        assert operation.status == OperationStatus.COMPLETED

        assert (
            operation.provider_payment_id
            == provider_payment_id
        )

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_find_retryable_operation_ids_returns_only_due_jobs(
    running_operation,
    send_job_service,
    retry_worker,
):
    # Arrange
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Provider returned 503",
        )

        await session.commit()

    # Force the retry time into the past.
    async with TestSessionLocal() as session:
        operation = await send_job_service.get_operation_for_update(
            session,
            running_operation,
        )

        assert operation.send_job is not None

        operation.send_job.next_retry_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )

        await session.commit()

    # Act
    async with TestSessionLocal() as session:
        operation_ids = (
            await retry_worker._find_retryable_operation_ids(
                session,
            )
        )

    # Assert
    assert running_operation in operation_ids


@pytest.mark.asyncio
async def test_find_retryable_operation_ids_skips_jobs_scheduled_for_future(
    running_operation,
    send_job_service,
    retry_worker,
):
    # Arrange
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Provider returned 503",
        )

        operation = await send_job_service.get_operation_for_update(
            session,
            running_operation,
        )

        assert operation.send_job is not None

        operation.send_job.next_retry_at = (
            datetime.now(timezone.utc)
            + timedelta(hours=1)
        )

        await session.commit()

    # Act
    async with TestSessionLocal() as session:
        operation_ids = (
            await retry_worker._find_retryable_operation_ids(
                session,
            )
        )

    # Assert
    assert running_operation not in operation_ids


@pytest.mark.asyncio
async def test_retry_worker_reuses_same_operation_id_after_lost_provider_response(
    running_operation,
    provider_client,
    running_worker,
    retry_worker,
):
    # Arrange
    provider_payment_id = "provider-payment-idempotent"

    # First request reaches the provider successfully,
    # but the client loses the HTTP response.
    provider_client.create_payment = AsyncMock(
        side_effect=[
            RuntimeError("Connection reset after provider accepted payment"),
            ProviderPayment(
                provider_payment_id=provider_payment_id,
                status="ACCEPTED",
            ),
        ],
    )

    # Act 1: initial attempt
    await running_worker._process_job(
        running_operation,
    )

    # The first attempt must have failed locally.
    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.WAITING_RETRY

    # Act 2: retry
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    assert provider_client.create_payment.await_count == 2

    calls = provider_client.create_payment.await_args_list

    # Both attempts must use exactly the same operation ID.
    assert calls[0].kwargs["operation_id"] == running_operation
    assert calls[1].kwargs["operation_id"] == running_operation

    # The payment body must remain unchanged across retries.
    assert calls[0].kwargs["amount"] == calls[1].kwargs["amount"]
    assert calls[0].kwargs["currency"] == calls[1].kwargs["currency"]

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        assert (
            operation.provider_payment_id
            == provider_payment_id
        )

        # Final payment status is still PROCESSING.
        # Only the callback can change it.
        assert operation.status == OperationStatus.PROCESSING

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_retry_worker_completes_job_after_multiple_failed_retries(
    running_operation,
    provider_client,
    running_worker,
    retry_worker,
):
    # Arrange
    provider_payment_id = "provider-payment-after-retries"

    # First attempt fails.
    provider_client.create_payment = AsyncMock(
        side_effect=[
            RuntimeError("Initial provider failure"),
            RuntimeError("Provider still unavailable"),
            ProviderPayment(
                provider_payment_id=provider_payment_id,
                status="ACCEPTED",
            ),
        ],
    )

    # Act
    # Initial RUNNING attempt.
    await running_worker._process_job(
        running_operation,
    )

    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.WAITING_RETRY
        assert send_job.attempt == 1

    # First retry fails.
    await retry_worker._process_job(
        running_operation,
    )

    async with TestSessionLocal() as session:
        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.WAITING_RETRY
        assert send_job.attempt == 2

    # Second retry succeeds.
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    assert provider_client.create_payment.await_count == 3

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None
        assert operation.status == OperationStatus.PROCESSING
        assert (
            operation.provider_payment_id
            == provider_payment_id
        )

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE

        # Two failed attempts happened before success.
        assert send_job.attempt == 2

        assert send_job.next_retry_at is None
        assert send_job.last_error is None


@pytest.mark.asyncio
async def test_retry_worker_uses_same_payment_data_on_every_retry(
    running_operation,
    provider_client,
    running_worker,
    retry_worker,
):
    # Arrange
    provider_payment_id = "provider-payment-same-body"

    provider_client.create_payment = AsyncMock(
        side_effect=[
            RuntimeError("Connection reset"),
            RuntimeError("Provider unavailable"),
            ProviderPayment(
                provider_payment_id=provider_payment_id,
                status="ACCEPTED",
            ),
        ],
    )

    # Act
    await running_worker._process_job(
        running_operation,
    )

    await retry_worker._process_job(
        running_operation,
    )

    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    assert provider_client.create_payment.await_count == 3

    calls = provider_client.create_payment.await_args_list

    for call in calls:
        assert call.kwargs["operation_id"] == running_operation
        assert call.kwargs["amount"] == Decimal("1000.00")
        assert call.kwargs["currency"] == "RUB"

    # The request payload must be identical on every attempt.
    assert calls[0].kwargs == calls[1].kwargs
    assert calls[1].kwargs == calls[2].kwargs


@pytest.mark.asyncio
async def test_retry_worker_persists_provider_payment_id(
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    provider_payment_id = "provider-payment-persisted"

    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Initial failure",
        )

        await session.commit()

    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id=provider_payment_id,
            status="ACCEPTED",
        ),
    )

    # Act
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        assert (
            operation.provider_payment_id
            == provider_payment_id
        )

        assert operation.status == OperationStatus.PROCESSING

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_retry_worker_does_not_overwrite_provider_payment_id_from_callback(
    client,
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    provider_payment_id = "provider-payment-from-callback"

    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Initial provider failure",
        )

        await session.commit()

    # The callback establishes the provider payment identity
    # before RetryWorker receives the provider response.
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

    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id=provider_payment_id,
            status="ACCEPTED",
        ),
    )

    # Act
    await retry_worker._process_job(
        running_operation,
    )

    # Assert
    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        # Callback remains the source of the provider identity.
        assert (
            operation.provider_payment_id
            == provider_payment_id
        )

        # Callback already finalized the payment.
        assert operation.status == OperationStatus.COMPLETED

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_retry_worker_does_not_overwrite_conflicting_provider_payment_id(
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    existing_provider_payment_id = "provider-payment-existing"
    conflicting_provider_payment_id = "provider-payment-conflict"

    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Initial failure",
        )

        operation = await send_job_service.get_operation_for_update(
            session,
            running_operation,
        )

        operation.provider_payment_id = (
            existing_provider_payment_id
        )

        await session.commit()

    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id=conflicting_provider_payment_id,
            status="ACCEPTED",
        ),
    )

    # Act
    #
    # The provider returned a different providerPaymentId
    # than the one already associated with the operation.
    #
    # This is a consistency violation.
    # complete_job() must reject the response rather than
    # overwrite the existing providerPaymentId.
    with pytest.raises(
        ValueError,
        match="Provider payment ID conflict",
    ):
        await retry_worker._process_job(
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

        # The original providerPaymentId must never be overwritten.
        assert (
            operation.provider_payment_id
            == existing_provider_payment_id
        )

        # The operation remains PROCESSING because only
        # a provider receipt can determine the final state.
        assert operation.status == OperationStatus.PROCESSING

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None

        # The conflicting provider response must not complete
        # the delivery job.
        assert send_job.state == SendJobState.WAITING_RETRY


@pytest.mark.asyncio
async def test_retry_worker_resumes_waiting_retry_after_worker_restart(
    running_operation,
    provider_client,
    send_job_service,
):
    # Arrange
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Provider unavailable before restart",
        )

        await session.commit()

    # Simulate the first worker/process disappearing.
    first_worker = RetryWorker(
        session_factory=TestSessionLocal,
        send_job_service=send_job_service,
        provider_client=provider_client,
        poll_interval=0.01,
    )

    del first_worker

    # A new worker starts after the process restart.
    restarted_worker = RetryWorker(
        session_factory=TestSessionLocal,
        send_job_service=send_job_service,
        provider_client=provider_client,
        poll_interval=0.01,
    )

    provider_payment_id = "provider-payment-after-restart"

    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id=provider_payment_id,
            status="ACCEPTED",
        ),
    )

    # Act
    await restarted_worker._process_job(
        running_operation,
    )

    # Assert
    provider_client.create_payment.assert_awaited_once()

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None

        assert (
            operation.provider_payment_id
            == provider_payment_id
        )

        # Provider acceptance does not complete the payment.
        assert operation.status == OperationStatus.PROCESSING

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_retry_worker_does_not_process_retry_before_next_retry_at(
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Provider unavailable",
        )

        operation = await send_job_service.get_operation_for_update(
            session,
            running_operation,
        )

        assert operation.send_job is not None

        operation.send_job.next_retry_at = (
            datetime.now(timezone.utc)
            + timedelta(hours=1)
        )

        await session.commit()

    provider_client.create_payment = AsyncMock()

    # Act
    async with TestSessionLocal() as session:
        retryable_ids = (
            await retry_worker._find_retryable_operation_ids(
                session,
            )
        )

    # Assert
    assert running_operation not in retryable_ids

    provider_client.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_retry_job_is_not_picked_up_after_restart(
    running_operation,
    provider_client,
    retry_worker,
    send_job_service,
):
    # Arrange
    provider_payment_id = "provider-payment-done"

    async with TestSessionLocal() as session:
        await send_job_service.move_to_retry(
            session=session,
            operation_id=running_operation,
            error="Initial failure",
        )

        await session.commit()

    provider_client.create_payment = AsyncMock(
        return_value=ProviderPayment(
            provider_payment_id=provider_payment_id,
            status="ACCEPTED",
        ),
    )

    # Complete the retry.
    await retry_worker._process_job(
        running_operation,
    )

    # Simulate worker restart.
    restarted_worker = RetryWorker(
        session_factory=TestSessionLocal,
        send_job_service=send_job_service,
        provider_client=provider_client,
        poll_interval=0.01,
    )

    # Act
    async with TestSessionLocal() as session:
        retryable_ids = (
            await restarted_worker._find_retryable_operation_ids(
                session,
            )
        )

    # Assert
    assert running_operation not in retryable_ids

    # No second provider request should be made.
    assert provider_client.create_payment.await_count == 1


@pytest.mark.asyncio
async def test_provider_response_lost_then_retry_succeeds(
    running_operation,
    real_provider_client,
    send_job_service,
    provider_requests,
):

    # 1. Arrange

    running_operation_id = running_operation

    # We use a real ProviderClient backed by MockTransport.
    # The mock provider:
    #
    #   first request  -> network error
    #   second request -> 202 + providerPaymentId
    #
    # The test therefore exercises the real HTTP client code.
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

    # 2. Act: first attempt

    # The operation is already:
    #
    #   Operation = PROCESSING
    #   SendJob = RUNNING
    #
    # RunningWorker performs the first HTTP attempt.
    await running_worker._process_job(running_operation_id)

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation_id,
        )

        assert operation is not None

        # The payment has not been finalized.
        assert operation.status == OperationStatus.PROCESSING

        # The providerPaymentId is not known locally yet.
        assert operation.provider_payment_id is None

        send_job = await session.get(
            SendJob,
            running_operation_id,
        )

        assert send_job is not None

        # The failed RunningWorker attempt moved the job
        # into the retry flow.
        assert send_job.state == SendJobState.WAITING_RETRY

        # One failed HTTP attempt has been recorded.
        assert send_job.attempt == 1

        assert send_job.last_error is not None

        # A retry must have been scheduled.
        assert send_job.next_retry_at is not None

    # Exactly one provider request has been made so far.
    assert len(provider_requests) == 1

    first_request = provider_requests[0]

    # The test calls the retry processing method directly.
    #
    # In production, RetryWorker would find the job through
    # find_retryable_operation_ids() and process it in its loop.
    await retry_worker._process_job(running_operation_id)

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation_id,
        )

        assert operation is not None

        # The callback is the only thing allowed to finalize
        # the payment operation.
        #
        # Therefore the provider's HTTP 202 must NOT change
        # PROCESSING into COMPLETED.
        assert operation.status == OperationStatus.PROCESSING

        # The providerPaymentId returned by the retry is now stored.
        assert (
            operation.provider_payment_id
            == "provider-payment-retry"
        )

        send_job = await session.get(
            SendJob,
            running_operation_id,
        )

        assert send_job is not None

        # The delivery job itself is complete.
        assert send_job.state == SendJobState.DONE

        # Retry metadata is cleared after successful delivery.
        assert send_job.next_retry_at is None
        assert send_job.last_error is None

        # attempt counts failed attempts, not successful attempts.
        assert send_job.attempt == 1

    assert len(provider_requests) == 2

    first_request = provider_requests[0]
    retry_request = provider_requests[1]

    assert (
        first_request.headers["Idempotency-Key"]
        == running_operation_id
    )

    assert (
        retry_request.headers["Idempotency-Key"]
        == running_operation_id
    )

    assert (
        first_request.headers["Idempotency-Key"]
        == retry_request.headers["Idempotency-Key"]
    )

    assert (
        first_request.headers["X-Correlation-ID"]
        == running_operation_id
    )

    assert (
        retry_request.headers["X-Correlation-ID"]
        == running_operation_id
    )

    assert first_request.content == retry_request.content



@pytest.mark.asyncio
async def test_retry_worker_keeps_job_in_waiting_retry_on_failure(
    running_operation,
    retry_worker,
    provider_client,
):

    # Arrange
    running_operation_id = running_operation

    # First move the job into WAITING_RETRY.
    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation_id,
        )

        assert operation is not None

        send_job = await session.get(
            SendJob,
            running_operation_id,
        )

        assert send_job is not None

        send_job.state = SendJobState.WAITING_RETRY
        send_job.attempt = 1
        send_job.next_retry_at = datetime.now(timezone.utc)

        await session.commit()

    provider_client.create_payment.side_effect = (
        RuntimeError("Retry provider failure")
    )

    # Act
    await retry_worker._process_job(running_operation_id)

    # Assert
    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation_id,
        )

        assert operation is not None

        assert operation.status == OperationStatus.PROCESSING

        send_job = await session.get(
            SendJob,
            running_operation_id,
        )

        assert send_job is not None

        # The retry worker must not leave the retry state.
        assert send_job.state == SendJobState.WAITING_RETRY

        # The failed retry increments the failed-attempt counter.
        assert send_job.attempt == 2

        assert send_job.last_error == (
            "Retry provider failure"
        )

        # Another retry must be scheduled.
        assert send_job.next_retry_at is not None

    provider_client.create_payment.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_worker_completes_waiting_retry_job(
    running_operation,
    retry_worker,
    provider_client,
):
    """
    WAITING_RETRY -> DONE

    Provider returns a providerPaymentId.
    Operation status remains PROCESSING.
    """

    # Arrange
    running_operation_id = running_operation

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation_id,
        )

        assert operation is not None

        send_job = await session.get(
            SendJob,
            running_operation_id,
        )

        assert send_job is not None

        send_job.state = SendJobState.WAITING_RETRY
        send_job.attempt = 1
        send_job.next_retry_at = datetime.now(timezone.utc)

        await session.commit()

    provider_client.create_payment.return_value = (
        ProviderPayment(
            provider_payment_id="provider-payment-retry",
            status="ACCEPTED",
        )
    )

    # Act
    await retry_worker._process_job(running_operation_id)

    # Assert
    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation_id,
        )

        assert operation is not None

        # HTTP 202 is not a final payment result.
        assert operation.status == OperationStatus.PROCESSING

        assert (
            operation.provider_payment_id
            == "provider-payment-retry"
        )

        send_job = await session.get(
            SendJob,
            running_operation_id,
        )

        assert send_job is not None

        assert send_job.state == SendJobState.DONE

        assert send_job.attempt == 1

        assert send_job.next_retry_at is None

        assert send_job.last_error is None

    provider_client.create_payment.assert_awaited_once_with(
        operation_id=running_operation_id,
        amount=Decimal("1000.00"),
        currency="RUB",
    )