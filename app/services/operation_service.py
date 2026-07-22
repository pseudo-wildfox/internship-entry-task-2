from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Operation, Event
from app.db.models.enums import OperationStatus
from app.schemas.operation import CreateOperationRequest


class OperationAlreadyExistsError(Exception):
    """Raised when an operation with the given ID already exists."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id

        super().__init__(
            f"Operation '{operation_id}' already exists"
        )

class OperationNotFoundError(Exception):
    """Raised when an operation with the given ID does not exist."""

    def __init__(self, operation_id: str) -> None:
        self.operation_id = operation_id

        super().__init__(
            f"Operation '{operation_id}' not found"
        )


class OperationService:
    async def create(
        self,
        session: AsyncSession,
        request: CreateOperationRequest,
    ) -> Operation:
        operation = Operation(
            operation_id=request.operation_id,
            amount=request.amount,
            currency=request.currency,
            description=request.description,
            status=OperationStatus.CREATED,
            provider_payment_id=None,
        )

        event = Event(
            operation=operation,
            sequence_no=1,
            type="CREATED",
            from_status=None,
            to_status=OperationStatus.CREATED,
            message="Operation created",
            occurred_at=datetime.now(timezone.utc),
        )

        session.add(operation)
        session.add(event)

        try:
            await session.commit()

        except IntegrityError as exc:
            await session.rollback()

            raise OperationAlreadyExistsError(
                request.operation_id
            ) from exc

        await session.refresh(operation)

        return operation


    async def get_by_id(
        self,
        session: AsyncSession,
        operation_id: str,
    ) -> Operation:
        operation = await session.get(
            Operation,
            operation_id,
        )

        if operation is None:
            raise OperationNotFoundError(operation_id)

        return operation

    async def get_events(
        self,
        session: AsyncSession,
        operation_id: str,
    ) -> list[Event]:
        operation = await session.get(
            Operation,
            operation_id,
        )

        if operation is None:
            raise OperationNotFoundError(operation_id)

        result = await session.execute(
            select(Event)
            .where(
                Event.operation_id == operation_id,
            )
            .order_by(
                Event.sequence_no.asc(),
            )
        )

        return list(result.scalars().all())