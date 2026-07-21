from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.payments import router
from app.db.database import check_connection


@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_connection()

    yield


app = FastAPI(
    lifespan=lifespan,
)

app.include_router(router=router)


