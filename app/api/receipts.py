from fastapi import APIRouter, Depends, Response, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.schemas.receipt import ReceiptRequest
from app.services.receipt_service import ReceiptService
from app.core.dependencies import get_receipt_service
from app.core.exceptions import ProviderPaymentConflictError, OperationNotFoundError

receipts_router = APIRouter()


@receipts_router.post(
    "/receipts",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def receive_receipt(
    receipt: ReceiptRequest,
    session: AsyncSession = Depends(get_db),
    receipt_service: ReceiptService = Depends(
        get_receipt_service,
    ),
) -> Response:
    try:
        await receipt_service.process_receipt(
            session=session,
            receipt=receipt,
        )

        await session.commit()

    except OperationNotFoundError as exc:
        await session.rollback()

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except ProviderPaymentConflictError as exc:
        await session.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )