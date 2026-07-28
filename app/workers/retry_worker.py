import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.provider.provider_client import ProviderClient
from app.services.send_job_service import SendJobService
from app.db.models.enums import SendJobState

logger = logging.getLogger(__name__)


class RetryWorker:
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
        logger.info("Retry worker started")

        try:
            while not self._stop_event.is_set():
                await self._process_retry_jobs()

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass

        finally:
            logger.info("Retry worker stopped")


    def stop(self) -> None:
        self._stop_event.set()


    async def _process_retry_jobs(self) -> None:
        async with self._session_factory() as session:
            operation_ids = await self._find_retryable_operation_ids(
                session,
            )

        for operation_id in operation_ids:
            try:
                await self._process_job(operation_id)

            except Exception:
                logger.exception(
                    "Failed to process retry job",
                    extra={
                        "operation_id": operation_id,
                    },
                )


    async def _find_retryable_operation_ids(
        self,
        session: AsyncSession,
    ) -> list[str]:
        return await self._send_job_service.find_retryable_operation_ids(session)


    async def _process_job(
        self,
        operation_id: str,
    ) -> None:
        # ---------------------------------------------------------
        # 1. Read the operation and verify that the job is still
        #    WAITING_RETRY.
        #
        #    No database transaction is kept open during the
        #    external HTTP request.
        # ---------------------------------------------------------

        async with self._session_factory() as session:
            operation = await self._send_job_service.get_operation(
                session,
                operation_id,
            )

            if operation is None:
                logger.error(
                    "Operation not found for retry job",
                    extra={
                        "operation_id": operation_id,
                    },
                )
                return

            if operation.send_job is None:
                logger.error(
                    "SendJob not found for retry job",
                    extra={
                        "operation_id": operation_id,
                    },
                )
                return

            if operation.send_job.state != SendJobState.WAITING_RETRY:
                return

            amount = operation.amount
            currency = operation.currency

        # ---------------------------------------------------------
        # 2. Retry the external provider request.
        #
        #    The same operation_id is used as Idempotency-Key and
        #    X-Correlation-ID by ProviderClient.
        #
        #    No database transaction is held here.
        # ---------------------------------------------------------

        try:
            provider_payment = await self._provider_client.create_payment(
                operation_id=operation_id,
                amount=amount,
                currency=currency,
            )

        except Exception as exc:
            # The job is already WAITING_RETRY.
            #
            # We do NOT call move_to_retry().
            # We only record the failed retry attempt and keep
            # the job in WAITING_RETRY.
            await self._record_retry_failure(
                operation_id=operation_id,
                error=str(exc),
            )
            return

        # ---------------------------------------------------------
        # 3. Provider accepted the request.
        #
        #    Persist providerPaymentId and mark the delivery job
        #    as DONE.
        #
        #    Operation.status is NOT changed here.
        #    The final payment status is determined by callback.
        # ---------------------------------------------------------

        await self._complete_job(
            operation_id=operation_id,
            provider_payment_id=(provider_payment.provider_payment_id),
        )


    async def _complete_job(
        self,
        operation_id: str,
        provider_payment_id: str,
    ) -> None:
        async with self._session_factory() as session:
            await self._send_job_service.complete_job(
                session=session,
                operation_id=operation_id,
                provider_payment_id=provider_payment_id,
            )

            await session.commit()


    async def _record_retry_failure(
        self,
        operation_id: str,
        error: str,
    ) -> None:
        async with self._session_factory() as session:
            await self._send_job_service.record_retry_failure(
                session=session,
                operation_id=operation_id,
                error=error,
            )

            await session.commit()
