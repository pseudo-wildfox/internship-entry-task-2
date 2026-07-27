import pytest
from sqlalchemy import select

from conftest import TestSessionLocal
from app.db.models import Operation, SendJob, Event
from app.db.models.enums import OperationStatus, SendJobState, EventType


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


@pytest.mark.asyncio
async def test_receipt_rejects_operation_and_establishes_provider_payment_id(
    client,
    running_operation,
):
    # Arrange
    provider_payment_id = "provider-payment-rejected"

    # Act
    response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": provider_payment_id,
            "operationId": running_operation,
            "result": "REJECTED",
            "message": "Payment rejected",
            "occurredAt": "2026-07-25T12:00:00Z",
        },
    )

    # Assert
    assert response.status_code == 204

    async with TestSessionLocal() as session:
        operation = await session.get(
            Operation,
            running_operation,
        )

        assert operation is not None
        assert operation.status == OperationStatus.REJECTED
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
async def test_duplicate_receipt_is_ignored_without_new_event(
    client,
    running_operation,
):
    # Arrange
    provider_payment_id = "provider-payment-duplicate"

    receipt = {
        "providerPaymentId": provider_payment_id,
        "operationId": running_operation,
        "result": "COMPLETED",
        "message": "Payment completed",
        "occurredAt": "2026-07-25T12:00:00Z",
    }

    first_response = await client.post(
        "/receipts",
        json=receipt,
    )

    assert first_response.status_code == 204

    async with TestSessionLocal() as session:
        events_before = (
            await session.scalars(
                select(Event)
                .where(
                    Event.operation_id == running_operation,
                )
            )
        ).all()

        event_count_before = len(events_before)

    # Act
    second_response = await client.post(
        "/receipts",
        json=receipt,
    )

    # Assert
    assert second_response.status_code == 204

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

        events_after = (
            await session.scalars(
                select(Event)
                .where(
                    Event.operation_id == running_operation,
                )
            )
        ).all()

        assert len(events_after) == event_count_before

        send_job = await session.get(
            SendJob,
            running_operation,
        )

        assert send_job is not None
        assert send_job.state == SendJobState.DONE


@pytest.mark.asyncio
async def test_late_conflicting_receipt_is_ignored_without_changing_final_status(
    client,
    running_operation,
):
    # Arrange
    provider_payment_id = "provider-payment-123"

    first_receipt_response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": provider_payment_id,
            "operationId": running_operation,
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-07-25T12:00:00Z",
        },
    )

    assert first_receipt_response.status_code == 204

    # Act
    conflicting_receipt_response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": provider_payment_id,
            "operationId": running_operation,
            "result": "REJECTED",
            "message": "Payment rejected later",
            "occurredAt": "2026-07-25T12:00:01Z",
        },
    )

    # Assert
    assert conflicting_receipt_response.status_code == 204

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

        events = (
            await session.scalars(
                select(Event)
                .where(
                    Event.operation_id == running_operation,
                )
                .order_by(Event.sequence_no),
            )
        ).all()

        # The conflicting late receipt must be recorded as ignored.
        ignored_events = [
            event
            for event in events
            if event.type == EventType.RECEIPT_IGNORED
        ]

        assert len(ignored_events) == 1

        ignored_event = ignored_events[0]

        assert (
            ignored_event.from_status
            == OperationStatus.COMPLETED
        )
        assert (
            ignored_event.to_status
            == OperationStatus.COMPLETED
        )


@pytest.mark.asyncio
async def test_duplicate_late_receipt_does_not_create_ignored_event(
    client,
    running_operation,
):
    # Arrange
    provider_payment_id = "provider-payment-duplicate"

    first_response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": provider_payment_id,
            "operationId": running_operation,
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-07-25T12:00:00Z",
        },
    )

    assert first_response.status_code == 204

    # Act
    duplicate_response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": provider_payment_id,
            "operationId": running_operation,
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-07-25T12:00:01Z",
        },
    )

    # Assert
    assert duplicate_response.status_code == 204

    async with TestSessionLocal() as session:
        events = (
            await session.scalars(
                select(Event)
                .where(
                    Event.operation_id == running_operation,
                )
            )
        ).all()

        ignored_events = [
            event
            for event in events
            if event.type == EventType.RECEIPT_IGNORED
        ]

        assert ignored_events == []


@pytest.mark.asyncio
async def test_receipt_can_complete_operation_before_provider_response(
    client,
    running_operation,
):
    # Arrange
    provider_payment_id = "provider-payment-early-callback"

    # Act
    response = await client.post(
        "/receipts",
        json={
            "providerPaymentId": provider_payment_id,
            "operationId": running_operation,
            "result": "COMPLETED",
            "message": "Payment completed",
            "occurredAt": "2026-07-25T12:00:00Z",
        },
    )

    # Assert
    assert response.status_code == 204

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