from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.enums import OperationStatus, EventType
from app.db.models import Event, Operation


class EventService:
    async def create_event(
        self,
        session: AsyncSession,
        *,
        operation: Operation,
        event_type: EventType,
        from_status: OperationStatus | None,
        to_status: OperationStatus,
        message: str,
    ) -> None:
        event = Event(
            operation=operation,
            sequence_no=await self._get_next_sequence_no(
                session=session,
                operation_id=operation.operation_id,
            ),
            type=event_type,
            from_status=from_status,
            to_status=to_status,
            message=message,
            occurred_at=datetime.now(timezone.utc),
        )

        session.add(event)

    async def _get_next_sequence_no(
        self,
        session: AsyncSession,
        operation_id: str,
    ) -> int:
        result = await session.execute(
            select(Event.sequence_no)
            .where(
                Event.operation_id == operation_id,
            )
            .order_by(
                Event.sequence_no.desc(),
            )
            .limit(1)
        )

        last_sequence_no = result.scalar_one_or_none()

        if last_sequence_no is None:
            return 1

        return last_sequence_no + 1

