import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.provider.provider_client import ProviderClient
from app.api.receipts import receipts_router
from app.core.settings import settings
from app.workers.pending_worker import PendingWorker
from app.api.health import health
from app.api.operations import operations_router
from app.db.database import check_connection, SessionLocal
from app.core.logging_config import setup_logging
from app.services.send_job_service import SendJobService
from app.workers.running_worker import RunningWorker

setup_logging()

logger = logging.getLogger(__name__)



@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_connection()

    pending_worker = PendingWorker(
        session_factory=SessionLocal,
        send_job_service=SendJobService.create_default(),
    )

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0),
    ) as http_client:
        app.state.http_client = http_client

        provider_client = ProviderClient(
            client=http_client, provider_url=settings.PROVIDER_URL
        )

        running_worker = RunningWorker(
            session_factory=SessionLocal,
            send_job_service=SendJobService.create_default(),
            provider_client=provider_client,
        )

        pending_task = asyncio.create_task(
            pending_worker.run(),
            name="pending-worker",
        )
        running_task = asyncio.create_task(
            running_worker.run(),
            name="running-worker",
        )
        try:
            yield
        finally:

            pending_worker.stop()
            await pending_task

            running_worker.stop()
            await running_task

app = FastAPI(
    lifespan=lifespan,
)

app.include_router(router=health)
app.include_router(router=operations_router)
app.include_router(router=receipts_router)

