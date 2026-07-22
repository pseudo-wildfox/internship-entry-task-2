import pytest
import asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_submit_operation(client, operation_id):
    response = await client.post(
        f"/operations/{operation_id}/submit",
    )

    assert response.status_code == 202

    data = response.json()

    assert data["operationId"] == operation_id
    assert data["status"] == "PROCESSING"
    assert data["providerPaymentId"] is None


@pytest.mark.asyncio
async def test_repeated_submit_returns_200(client, operation_id):
    first_submit = await client.post(
        f"/operations/{operation_id}/submit",
    )

    assert first_submit.status_code == 202

    second_submit = await client.post(
        f"/operations/{operation_id}/submit",
    )

    assert second_submit.status_code == 200


@pytest.mark.asyncio
async def test_submit_creates_event(client, operation_id):
    response = await client.post(
        f"/operations/{operation_id}/submit",
    )

    assert response.status_code == 202

    events_response = await client.get(
        f"/operations/{operation_id}/events",
    )

    assert events_response.status_code == 200

    events = events_response.json()

    assert len(events) == 2
    assert events[0]["eventId"] == 1
    assert events[0]["type"] == "CREATED"
    assert events[1]["eventId"] == 2
    assert events[1]["type"] == "SUBMIT_REQUESTED"


@pytest.mark.asyncio
async def test_repeated_submit_does_not_create_event(client, operation_id):
    first_submit = await client.post(
        f"/operations/{operation_id}/submit",
    )

    assert first_submit.status_code == 202

    second_submit = await client.post(
        f"/operations/{operation_id}/submit",
    )

    assert second_submit.status_code == 200

    events_response = await client.get(
        f"/operations/{operation_id}/events",
    )

    events = events_response.json()

    assert len(events) == 2
    assert [event["eventId"] for event in events] == [1, 2]


@pytest.mark.asyncio
async def test_submit_nonexistent_operation_returns_404(client):
    response = await client.post(
        "/operations/nonexistent-operation/submit",
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_submit_operation(client, operation_id):
    async def submit():
        return await client.post(
            f"/operations/{operation_id}/submit",
        )

    responses = await asyncio.gather(*(submit() for _ in range(10)))

    status_codes = [response.status_code for response in responses]

    assert status_codes.count(202) == 1
    assert status_codes.count(200) == 9

    for response in responses:
        data = response.json()

        assert data["operationId"] == operation_id
        assert data["status"] == "PROCESSING"
        assert data["providerPaymentId"] is None

    events_response = await client.get(
        f"/operations/{operation_id}/events",
    )

    assert events_response.status_code == 200

    events = events_response.json()

    assert len(events) == 2

    assert events[0]["eventId"] == 1
    assert events[0]["type"] == "CREATED"
    assert events[0]["fromStatus"] is None
    assert events[0]["toStatus"] == "CREATED"

    assert events[1]["eventId"] == 2
    assert events[1]["type"] == "SUBMIT_REQUESTED"
    assert events[1]["fromStatus"] == "CREATED"
    assert events[1]["toStatus"] == "PROCESSING"
