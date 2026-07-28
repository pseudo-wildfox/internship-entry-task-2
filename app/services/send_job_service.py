from datetime import datetime, timezone

from sqlalchemy import update, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.send_job import SendJob
from app.db.models.enums import SendJobState
from app.db.models import Operation
from core.exceptions import OperationNotFoundError
from services.retry_policy import RetryPolicy


class SendJobService:
    def __init__(self, retry_policy: RetryPolicy):
        self._retry_policy = retry_policy

    @classmethod
    def create_default(cls) -> Self:
        return cls(
            retry_policy=RetryPolicy()
        )

    async def claim_send_job(
        self,
        session: AsyncSession,
        operation_id: str,
    ) -> bool:
        """
        Atomically claims a PENDING SendJob.

        Operation is the serialization point for all state changes
        related to an operation.

        PENDING -> RUNNING

        Returns True if this worker successfully claimed the job.
        Returns False if the operation or SendJob does not exist,
        or the job was already claimed.
        """

        operation = await self.get_operation_for_update(
            session=session,
            operation_id=operation_id,
        )

        send_job = operation.send_job

        if send_job is None:
            return False

        if send_job.state != SendJobState.PENDING:
            return False

        send_job.state = SendJobState.RUNNING
        send_job.updated_at = datetime.now(timezone.utc)

        return True


    async def get_operation_for_update(
        self,
        session: AsyncSession,
        operation_id: str,
    ) -> Operation:
        """
        Operation is the serialization point for all state changes
        related to an operation.
        """
        result = await session.execute(
            select(Operation)
            .options(
                selectinload(Operation.send_job),
            )
            .where(Operation.operation_id == operation_id)
            .with_for_update()
        )

        operation = result.scalar_one_or_none()

        if operation is None:
            raise OperationNotFoundError(
                f"Operation not found: {operation_id}",
            )

        return operation


    async def get_operation(
        self,
        session: AsyncSession,
        operation_id: str,
    ) -> Operation | None:
        result = await session.execute(
            select(Operation)
            .options(
                selectinload(Operation.send_job),
            )
            .where(
                Operation.operation_id == operation_id,
            )
        )

        return result.scalar_one_or_none()


    async def complete_job(
        self,
        session: AsyncSession,
        operation_id: str,
        provider_payment_id: str,
    ) -> None:

        operation = await self.get_operation_for_update(
            session,
            operation_id,
        )

        send_job = operation.send_job

        if send_job is None:
            raise ValueError(f"SendJob not found: {operation_id}")

        # The callback could have set the provider_payment_id before
        # we received HTTP 202 from the provider.
        #
        # If the ID has already been set, it must match the one
        # returned by the provider.
        if (
            operation.provider_payment_id is not None
            and operation.provider_payment_id != provider_payment_id
        ):
            raise ValueError(
                "Provider payment ID conflict: "
                f"operation={operation_id}, "
                f"existing={operation.provider_payment_id}, "
                f"received={provider_payment_id}"
            )

        # The Job has already been processed.
        #
        # For example, the callback could have arrived before the HTTP response
        # from the provider and already changed the SendJob state.
        #
        # In this case, the late response from the provider should not rollback
        # or reprocess the operation.

        if send_job.state != SendJobState.RUNNING:
            return

        # If the callback has already set the provider_payment_id,
        # there is no need to assign it again.
        if operation.provider_payment_id is None:
            operation.provider_payment_id = provider_payment_id

        # The provider accepted the payment request.
        # This only completes the delivery job.
        #
        # Operation status MUST NOT be changed here.
        # The final payment status is determined exclusively
        # by the provider receipt callback.
        send_job.state = SendJobState.DONE


    async def move_to_retry(
        self,
        session: AsyncSession,
        operation_id: str,
        error: str,
    ) -> None:
        operation = await self.get_operation_for_update(
            session,
            operation_id,
        )

        send_job = operation.send_job

        if send_job is None:
            raise ValueError(f"SendJob not found: {operation_id}")

        if send_job.state != SendJobState.RUNNING:
            return

        now = datetime.now(timezone.utc)
        send_job.state = SendJobState.WAITING_RETRY

        await self._schedule_retry(
            send_job,
            error=error,
            now=now,
        )
        send_job.updated_at = now


    async def _schedule_retry(
            self,
            send_job: SendJob,
            *,
            error: str,
            now: datetime,
    ) -> None:
        next_attempt = send_job.attempt + 1

        send_job.attempt = next_attempt
        send_job.last_error = error