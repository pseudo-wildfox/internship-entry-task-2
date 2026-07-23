import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import SendJob
from app.db.models.enums import SendJobState
from services.send_job_service import SendJobService

logger = logging.getLogger(__name__)


class PendingWorker:

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        send_job_service: SendJobService,
        poll_interval: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._send_job_service = send_job_service
        self._poll_interval = poll_interval

        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        logger.info("Pending worker started")

        while not self._stop_event.is_set():
            try:
                await self._process_pending_jobs()

            except Exception:
                logger.exception(
                    "Unexpected error in pending worker"
                )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval,
                )

            except asyncio.TimeoutError:
                pass

        logger.info("Pending worker stopped")

    def stop(self) -> None:
        self._stop_event.set()

    async def _process_pending_jobs(self) -> None:
        async with self._session_factory() as session:

            operation_ids = await self._find_pending_operation_ids(
                session
            )

        for operation_id in operation_ids:
            await self._claim_job(operation_id)


    async def _find_pending_operation_ids(
        self,
        session: AsyncSession,
    ) -> list[str]:

        result = await session.execute(
            select(SendJob.operation_id)
            .where(
                SendJob.state == SendJobState.PENDING,
            )
            .order_by(
                SendJob.created_at,
            )
        )

        return list(result.scalars().all())


    async def _claim_job(
        self,
        operation_id: str,
    ) -> None:

        async with self._session_factory() as session:

            async with session.begin():

                claimed = await self._send_job_service.claim_send_job(
                    session=session,
                    operation_id=operation_id,
                )

            if not claimed:
                logger.debug(
                    "Send job was already claimed",
                    extra={
                        "operation_id": operation_id,
                    },
                )

                return

            logger.info(
                "Send job claimed",
                extra={
                    "operation_id": operation_id,
                },
            )