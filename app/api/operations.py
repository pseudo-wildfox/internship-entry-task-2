from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.db.database import get_db
from app.schemas.operation import (
    CreateOperationRequest,
    OperationResponse,
    EventResponse,
)
from app.services.operation_service import (
    OperationService, SubmitOutcome,
)
from core.dependencies import get_operation_service
from core.exceptions import OperationAlreadyExistsError, OperationNotFoundError

operations_router = APIRouter(
    prefix="/operations",
    tags=["operations"],
)



@operations_router.post(
    "",
    response_model=OperationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operation(
    request: CreateOperationRequest,
    session: AsyncSession = Depends(get_db),
    operation_service: OperationService = Depends(get_operation_service),
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


@operations_router.get(
    "/{operation_id}/events",
    response_model=list[EventResponse],
    status_code=status.HTTP_200_OK,
)
async def get_operation_events(
    operation_id: str,
    session: AsyncSession = Depends(get_db),
    operation_service: OperationService = Depends(get_operation_service)
) -> list[EventResponse]:
    try:
        events = await operation_service.get_events(
            session=session,
            operation_id=operation_id,
        )

    except OperationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return [
        EventResponse.model_validate(event)
        for event in events
    ]


@operations_router.post(
    "/{operation_id}/submit",
    response_model=OperationResponse,
)
async def submit_operation(
    operation_id: str,
    session: AsyncSession = Depends(get_db),
    operation_service: OperationService = Depends(get_operation_service)
) -> JSONResponse:
    try:
        result = await operation_service.submit(
            session=session,
            operation_id=operation_id,
        )

    except OperationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    response_status = (
        status.HTTP_202_ACCEPTED
        if result.outcome == SubmitOutcome.CREATED
        else status.HTTP_200_OK
    )

    return JSONResponse(
        status_code=response_status,
        content=OperationResponse.model_validate(
            result.operation,
        ).model_dump(
            by_alias=True,
            mode="json",
        ),
    )


@operations_router.get(
    "/{operation_id}",
    response_model=OperationResponse,
    status_code=status.HTTP_200_OK,
)
async def get_operation(
    operation_id: str,
    session: AsyncSession = Depends(get_db),
    operation_service: OperationService = Depends(get_operation_service)
) -> OperationResponse:
    try:
        operation = await operation_service.get_by_id(
            session=session,
            operation_id=operation_id,
        )

    except OperationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return OperationResponse.model_validate(operation)