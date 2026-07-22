from enum import StrEnum


class OperationStatus(StrEnum):
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class SendJobState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_RETRY = "WAITING_RETRY"
    DONE = "DONE"


'''
| `type`              | `fromStatus`           | `toStatus`             | Смысл                                           |
| ------------------- | ---------------------- | ---------------------- | ----------------------------------------------- |
| `CREATED`           | `null`                 | `CREATED`              | Операция создана                                |
| `SUBMIT_REQUESTED`  | `CREATED`              | `PROCESSING`           | Надёжно зафиксировано намерение отправки        |
| `RECEIPT_COMPLETED` | `PROCESSING`           | `COMPLETED`            | Получена подтверждающая квитанция               |
| `RECEIPT_REJECTED`  | `PROCESSING`           | `REJECTED`             | Получена квитанция об отказе                    |
| `RECEIPT_IGNORED`   | `COMPLETED`/`REJECTED` | `COMPLETED`/`REJECTED` | Конфликтующая поздняя квитанция проигнорирована |

'''

class EventType(StrEnum):
    CREATED = "CREATED"
    SUBMIT_REQUESTED = "SUBMIT_REQUESTED"
    RECEIPT_COMPLETED = "RECEIPT_COMPLETED"
    RECEIPT_REJECTED = "RECEIPT_REJECTED"
    RECEIPT_IGNORED = "RECEIPT_IGNORED"