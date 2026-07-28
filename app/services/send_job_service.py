from datetime import datetime, timezone
from typing import Self

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
        """
        Completes the delivery job after the provider accepted the request.

        RUNNING -> DONE
        WAITING_RETRY -> DONE

        Operation.status is intentionally not changed here.
        The final payment status is determined exclusively by receipt callbacks.

        The operation row is locked before any mutation.
        """

        operation = await self.get_operation_for_update(
            session,
            operation_id,
        )

        send_job = operation.send_job

        if send_job is None:
            raise ValueError(
                f"SendJob not found: {operation_id}"
            )

        # A callback may have established the provider payment ID
        # before the HTTP response was received.
        #
        # If the ID is already known, the provider response must
        # contain the same ID.
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

        # The delivery job may already have been completed by another
        # code path. In that case, the late provider response must not
        # change the operation state or create another transition.
        if send_job.state == SendJobState.DONE:
            return

        # If the callback has already moved the job out of RUNNING
        # and the state is no longer eligible for completion, do nothing.
        if send_job.state not in {
            SendJobState.RUNNING,
            SendJobState.WAITING_RETRY,
        }:
            return

        if operation.provider_payment_id is None:
            operation.provider_payment_id = provider_payment_id

        now = datetime.now(timezone.utc)

        send_job.state = SendJobState.DONE
        send_job.next_retry_at = None
        send_job.last_error = None
        send_job.updated_at = now


    async def move_to_retry(
        self,
        session: AsyncSession,
        operation_id: str,
        error: str,
    ) -> None:
        """
        Moves a RUNNING SendJob into the retry flow.

        RUNNING -> WAITING_RETRY

        The first failed attempt is recorded here.
        The next retry time is calculated by RetryPolicy.

        The operation row is locked before any state mutation.
        """

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

        await self._schedule_retry(
            send_job,
            error=error,
            now=now,
        )

        send_job.state = SendJobState.WAITING_RETRY
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
        send_job.next_retry_at = (
            self._retry_policy.next_retry_at(
                attempt=next_attempt,
                now=now,
            )
        )


    async def find_retryable_operation_ids(
        self,
        session: AsyncSession,
    ) -> list[str]:
        """
        Finds SendJobs whose retry time has been reached.

        WAITING_RETRY jobs always have a non-null next_retry_at.
        """

        now = datetime.now(timezone.utc)

        result = await session.scalars(
            select(SendJob.operation_id)
            .where(
                SendJob.state == SendJobState.WAITING_RETRY,
                SendJob.next_retry_at <= now,
            )
            .order_by(
                SendJob.next_retry_at.asc(),
                SendJob.created_at.asc(),
            ),
        )

        return list(result.all())


    async def record_retry_failure(
        self,
        session: AsyncSession,
        operation_id: str,
        error: str,
    ) -> None:
        """
        Records a failed retry attempt.

        WAITING_RETRY -> WAITING_RETRY

        The state does not change.
        Retry metadata is updated and the next retry is scheduled.

        The operation row is locked before any mutation.
        """

        operation = await self.get_operation_for_update(
            session,
            operation_id,
        )

        send_job = operation.send_job

        if send_job is None:
            raise ValueError(
                f"SendJob not found: {operation_id}"
            )

        if send_job.state != SendJobState.WAITING_RETRY:
            return

        now = datetime.now(timezone.utc)

        await self._schedule_retry(
            send_job,
            error=error,
            now=now,
        )

        send_job.updated_at = now