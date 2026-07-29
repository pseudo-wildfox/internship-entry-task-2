from fastapi import Depends

from app.services.event_service import EventService
from app.services.receipt_service import ReceiptService
from app.services.send_job_service import SendJobService
from app.services.operation_service import OperationService


def get_send_job_service() -> SendJobService:
    return SendJobService.create_default()


def get_event_service() -> EventService:
    return EventService()


def get_receipt_service(
    send_job_service: SendJobService = Depends(
        get_send_job_service,
    ),
    event_service: EventService = Depends(
        get_event_service,
    ),
) -> ReceiptService:
    return ReceiptService(
        send_job_service=send_job_service,
        event_service=event_service,
    )

def get_operation_service(
        event_service: EventService = Depends(
            get_event_service,
        )
) -> OperationService:
    return OperationService(
        event_service=event_service,
    )