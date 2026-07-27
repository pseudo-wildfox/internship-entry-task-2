from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from app.schemas.receipt import ReceiptRequest
from app.services.send_job_service import SendJobService
from app.db.models.enums import OperationStatus, SendJobState
from app.services.event_service import EventService
from app.db.models.enums import EventType
from app.db.models import Operation


class ReceiptService:
    def __init__(
        self,
        send_job_service: SendJobService,
        event_service: EventService,
    ) -> None:
        self._send_job_service = send_job_service
        self._event_service = event_service

    async def process_receipt(
        self,
        session: AsyncSession,
        receipt: ReceiptRequest,
    ) -> None:
        operation = await self._send_job_service.get_operation_for_update(
            session=session,
            operation_id=receipt.operation_id,
        )

        # 1. ProviderPaymentId conflict
        if (
            operation.provider_payment_id is not None
            and operation.provider_payment_id
            != receipt.provider_payment_id
        ):
            raise ValueError(
                "Provider payment ID conflict: "
                f"operation={receipt.operation_id}, "
                f"existing={operation.provider_payment_id}, "
                f"received={receipt.provider_payment_id}",
            )

        # 2. First valid receipt.
        #
        # The receipt establishes the relation:
        #
        # Operation <-> Provider Payment
        #
        if operation.provider_payment_id is None:
            operation.provider_payment_id = (
                receipt.provider_payment_id
            )

        # 3. Receipt for an already finalized operation.
        if operation.status != OperationStatus.PROCESSING:
            await self._handle_late_receipt(
                session=session,
                operation=operation,
                receipt=receipt,
            )
            return

        # 4. First final receipt.
        await self._complete_operation(
            session=session,
            operation=operation,
            receipt=receipt,
        )


    async def _handle_late_receipt(
            self,
            session: AsyncSession,
            operation: Operation,
            receipt: ReceiptRequest,
    ) -> None:
        current_status = operation.status
        receipt_status = OperationStatus(receipt.result)

        # Same final result:
        #
        # COMPLETED + COMPLETED
        # REJECTED  + REJECTED
        #
        # This is a duplicate receipt.
        # Nothing should be changed.
        if current_status == receipt_status:
            return

        # Different final result:
        #
        # COMPLETED + REJECTED
        # REJECTED + COMPLETED
        #
        # The operation is already finalized.
        # The late conflicting receipt is ignored,
        # but the fact that it happened is recorded.
        await self._event_service.create_event(
            session=session,
            operation=operation,
            event_type=EventType.RECEIPT_IGNORED,
            from_status=current_status,
            to_status=current_status,
            message=(
                "Conflicting late receipt ignored: "
                f"received={receipt.result}; "
                f"current={current_status.value}"
            ),
        )

    async def _complete_operation(
            self,
            session: AsyncSession,
            operation: Operation,
            receipt: ReceiptRequest,
    ) -> None:
        old_status = operation.status

        new_status = OperationStatus(receipt.result)

        operation.status = new_status

        if operation.send_job is not None:
            operation.send_job.state = SendJobState.DONE

        event_type = self._handle_status(receipt.result)

        await self._event_service.create_event(
            session=session,
            operation=operation,
            event_type=event_type,
            from_status=old_status,
            to_status=new_status,
            message=receipt.message,
        )


    def _handle_status(
            self,
            status: Literal["COMPLETED", "REJECTED"],
    ) -> EventType:
        match status:
            case "COMPLETED":
                return EventType.RECEIPT_COMPLETED

            case "REJECTED":
                return EventType.RECEIPT_REJECTED

            case _:
                raise ValueError(
                    f"Unsupported receipt status: {status}",
                )
