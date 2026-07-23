import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from workers.pending_worker import PendingWorker
from app.api.health import health
from app.api.operations import router
from app.db.database import check_connection, SessionLocal
from app.core.logging_config import setup_logging
from app.services.send_job_service import SendJobService

setup_logging()

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_connection()

    pending_worker = PendingWorker(
        session_factory=SessionLocal,
        send_job_service=SendJobService(),
    )

    pending_task = asyncio.create_task(
        pending_worker.run(),
        name="pending-worker",
    )
    try:
        yield
    finally:

        pending_worker.stop()
        await pending_task

app = FastAPI(
    lifespan=lifespan,
)

app.include_router(router=health)
app.include_router(router=router)

