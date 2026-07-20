from enum import Enum


class OperationStatus(str, Enum):
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class SendJobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_RETRY = "WAITING_RETRY"
    DONE = "DONE"