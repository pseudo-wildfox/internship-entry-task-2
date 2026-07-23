from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.send_job import SendJob
from app.db.models.enums import SendJobState


class SendJobService:
    async def claim_send_job(
        self,
        session: AsyncSession,
        operation_id: str,
    ) -> bool:
        """
        Atomically claims a PENDING SendJob.

        PENDING -> RUNNING

        Returns True if this worker successfully claimed the job.
        Returns False if the job does not exist or was already claimed.
        """

        stmt = (
            update(SendJob)
            .where(
                SendJob.operation_id == operation_id,
                SendJob.state == SendJobState.PENDING,
            )
            .values(
                state=SendJobState.RUNNING,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(SendJob.operation_id)
        )

        result = await session.execute(stmt)

        claimed_operation_id = result.scalar_one_or_none()

        return claimed_operation_id is not None
