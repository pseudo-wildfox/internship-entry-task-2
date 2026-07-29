from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Operation, Event, SendJob
from app.db.models.enums import OperationStatus, SendJobState, EventType
from app.schemas.operation import CreateOperationRequest
from app.core.exceptions import OperationAlreadyExistsError, OperationNotFoundError
from app.services.event_service import EventService


class SubmitOutcome(StrEnum):
    CREATED = "CREATED"
    EXISTING = "EXISTING"


@dataclass(frozen=True)
class SubmitResult:
    operation: Operation
    outcome: SubmitOutcome


class OperationService:

    def __init__(
        self,
        event_service: EventService,
    ) -> None:
        self._event_service = event_service


    async def create(
        self,
        session: AsyncSession,
        request: CreateOperationRequest,
    ) -> Operation:

        await self.exists_of_throw_exception(session, request)

        operation = Operation(
            operation_id=request.operation_id,
            amount=request.amount,
            currency=request.currency,
            description=request.description,
            status=OperationStatus.CREATED,
            provider_payment_id=None,
        )

        session.add(operation)

        await self._event_service.create_event(
            session,
            operation=operation,
            event_type=EventType.CREATED,
            from_status=None,
            to_status=OperationStatus.CREATED,
            message="Operation created",
        )

        try:
            await session.commit()

        except IntegrityError as exc:
            await session.rollback()

            raise OperationAlreadyExistsError(
                request.operation_id
            ) from exc

        await session.refresh(operation)

        return operation


    async def exists_of_throw_exception(self, session, request):
        existing = await session.get(
            Operation,
            request.operation_id,
        )
        if existing is not None:
            raise OperationAlreadyExistsError(
                request.operation_id
            )


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


    async def submit(
            self,
            session: AsyncSession,
            operation_id: str,
    ) -> SubmitResult:
        result = await session.execute(
            select(Operation)
            .where(
                Operation.operation_id == operation_id,
            )
            .with_for_update()
        )
        operation = result.scalar_one_or_none()

        if operation is None:
            raise OperationNotFoundError(operation_id)

        if operation.status != OperationStatus.CREATED:
            return SubmitResult(
                operation=operation,
                outcome=SubmitOutcome.EXISTING,
            )

        send_job = SendJob(
            operation=operation,
            state=SendJobState.PENDING,
            attempt=0,
            next_retry_at=None,
            last_error=None,
        )
        session.add(send_job)

        operation.status = OperationStatus.PROCESSING

        await self._event_service.create_event(
            session,
            operation=operation,
            event_type=EventType.SUBMIT_REQUESTED,
            from_status=OperationStatus.CREATED,
            to_status=OperationStatus.PROCESSING,
            message="Payment submission requested",
        )

        await session.commit()

        await session.refresh(operation)

        return SubmitResult(
            operation=operation,
            outcome=SubmitOutcome.CREATED,
        )