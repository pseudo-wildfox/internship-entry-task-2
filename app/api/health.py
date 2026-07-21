from fastapi import APIRouter
from starlette import status

health = APIRouter(
    tags=["[payments]"],
)


@health.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {"status": "ok"}

