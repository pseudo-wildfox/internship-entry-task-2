from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


class ReceiptRequest(BaseModel):
    provider_payment_id: str = Field(alias="providerPaymentId")
    operation_id: str = Field(alias="operationId")
    result: Literal["COMPLETED", "REJECTED"]
    message: str
    occurred_at: datetime = Field(alias="occurredAt")

    model_config = ConfigDict(
        populate_by_name=True,
    )
