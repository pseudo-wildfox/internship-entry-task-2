from enum import StrEnum


class OperationStatus(StrEnum):
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


'''
    PENDING - Job еще ни разу не обрабатывался или готов к первой обработке.

    RUNNING - Один worker сейчас пытается отправить платеж.

    WAITING_RETRY - Последняя попытка не дала нам надежного результата. Нужно попробовать снова после next_retry_at.

    DONE - HTTP-взаимодействие с провайдером завершено настолько, что повторять отправку больше не нужно.
'''
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