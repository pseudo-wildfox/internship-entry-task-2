from fastapi import APIRouter
from starlette import status

router = APIRouter(
    tags=["[payments]"],
)


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {"status": "ok"}

