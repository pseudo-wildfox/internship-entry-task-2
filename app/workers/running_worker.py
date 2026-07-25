import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

from app.api.provider.provider_client import ProviderClient
from app.services.send_job_service import SendJobService
from app.db.models import SendJob
from app.db.models.enums import SendJobState

logger = logging.getLogger(__name__)


class RunningWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        send_job_service: SendJobService,
        provider_client: ProviderClient,
        poll_interval: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._send_job_service = send_job_service
        self._provider_client = provider_client
        self._poll_interval = poll_interval

        self._stop_event = asyncio.Event()


    async def run(self) -> None:
        logger.info("Running worker started")

        try:
            while not self._stop_event.is_set():
                await self._process_running_jobs()

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass

        finally:
            logger.info("Running worker stopped")


    def stop(self) -> None:
        self._stop_event.set()


    async def _process_running_jobs(self) -> None:
        async with self._session_factory() as session:
            operation_ids = await self._find_running_operation_ids(
                session,
            )
        for operation_id in operation_ids:
            try:
                await self._process_job(operation_id)
            except Exception:
                logger.exception(
                    "Failed to process running job",
                    extra={
                        "operation_id": operation_id,
                    },
                )


    async def _find_running_operation_ids(
        self,
        session: AsyncSession,
    ) -> list[str]:
        result = await session.scalars(
            select(SendJob.operation_id)
            .where(
                SendJob.state == SendJobState.RUNNING,
            )
            .order_by(SendJob.created_at),
        )

        return list(result.all())


    async def _process_job(
        self,
        operation_id: str,
    ) -> None:
        async with self._session_factory() as session:
            operation = await self._send_job_service.get_operation(
                session,
                operation_id,
            )

            if operation is None:
                logger.error(
                    "Operation not found",
                    extra={"operation_id": operation_id},
                )
                return

            if operation.send_job is None:
                logger.error(
                    "SendJob not found",
                    extra={"operation_id": operation_id},
                )
                return

            if operation.send_job.state != SendJobState.RUNNING:
                return

            amount = operation.amount
            currency = operation.currency

        try:
            provider_payment = await self._provider_client.create_payment(
                operation_id=operation_id,
                amount=amount,
                currency=currency,
            )
        except Exception as exc:
            await self._mark_job_for_retry(
                operation_id=operation_id,
                error=str(exc),
            )
            return

        async with self._session_factory() as session:
            await self._send_job_service.complete_job(
                session=session,
                operation_id=operation_id,
                provider_payment_id=provider_payment.provider_payment_id,
            )
            await session.commit()


    async def _mark_job_for_retry(
            self,
            operation_id: str,
            error: str,
    ) -> None:
        async with self._session_factory() as session:
            await self._send_job_service.move_to_retry(
                session=session,
                operation_id=operation_id,
                error=error,
            )
            await session.commit()

