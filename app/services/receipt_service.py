from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from app.schemas.receipt import ReceiptRequest
from app.services.send_job_service import SendJobService
from app.db.models.enums import OperationStatus, SendJobState
from app.services.event_service import EventService
from app.db.models.enums import EventType
from app.db.models import Operation
from app.core.exceptions import ProviderPaymentConflictError


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

        self._validate_provider_payment_id(
            operation=operation,
            receipt=receipt,
        )

        # The first valid receipt establishes the relationship
        # between our operation and the provider payment.
        if operation.provider_payment_id is None:
            operation.provider_payment_id = (
                receipt.provider_payment_id
            )

        # Operation is already finalized.
        #
        # Same result  -> duplicate receipt, ignore silently.
        # Different result -> late conflicting receipt,
        #                     record as ignored.
        if operation.status != OperationStatus.PROCESSING:
            await self._handle_late_receipt(
                session=session,
                operation=operation,
                receipt=receipt,
            )
            return

        # First valid final receipt.
        await self._complete_operation(
            session=session,
            operation=operation,
            receipt=receipt,
        )


    @staticmethod
    def _validate_provider_payment_id(
        operation: Operation,
        receipt: ReceiptRequest,
    ) -> None:
        if (
            operation.provider_payment_id is not None
            and operation.provider_payment_id
            != receipt.provider_payment_id
        ):
            raise ProviderPaymentConflictError(
                "Provider payment ID conflict: "
                f"operation={receipt.operation_id}, "
                f"existing={operation.provider_payment_id}, "
                f"received={receipt.provider_payment_id}",
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

        event_type = self._handle_status(
            receipt.result,
        )

        await self._event_service.create_event(
            session=session,
            operation=operation,
            event_type=event_type,
            from_status=old_status,
            to_status=new_status,
            message=receipt.message,
        )


    async def _handle_late_receipt(
        self,
        session: AsyncSession,
        operation: Operation,
        receipt: ReceiptRequest,
    ) -> None:
        current_status = operation.status
        receipt_status = OperationStatus(receipt.result)

        # Duplicate receipt.
        #
        # Example:
        #   Operation = COMPLETED
        #   Receipt   = COMPLETED
        #
        # No new event is created.
        if current_status == receipt_status:
            return

        # Conflicting late receipt.
        #
        # Example:
        #   Operation = COMPLETED
        #   Receipt   = REJECTED
        #
        # The final status must not change.
        # We only record the ignored receipt.
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


    @staticmethod
    def _handle_status(
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