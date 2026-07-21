from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.schemas.operation import (
    CreateOperationRequest,
    OperationResponse,
)
from app.services.operation_service import (
    OperationAlreadyExistsError,
    OperationService,
)


router = APIRouter(
    prefix="/operations",
    tags=["operations"],
)

# I don't mind global service because it's stateless
operation_service = OperationService()


@router.post(
    "",
    response_model=OperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operation(
    request: CreateOperationRequest,
    session: AsyncSession = Depends(get_db),
) -> OperationResponse:
    try:
        operation = await operation_service.create(
            session=session,
            request=request,
        )

    except OperationAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return OperationResponse.model_validate(operation)