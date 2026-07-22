from datetime import datetime

from decimal import Decimal

from pydantic import field_validator
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from app.db.models.enums import OperationStatus


def to_camel(value: str) -> str:
    parts = value.split("_")

    return parts[0] + "".join(
        part.capitalize()
        for part in parts[1:]
    )


class CreateOperationRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    operation_id: str = Field(
        ...,
        min_length=1,
        max_length=45,
    )

    amount: Decimal

    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
    )

    description: str | None = Field(
        default=None,
        max_length=255,
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("amount must be positive")

        if value.as_tuple().exponent < -2:
            raise ValueError(
                "amount must have no more than 2 decimal places"
            )

        return value

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if value != "RUB":
            raise ValueError("only RUB currency is supported")

        return value


class OperationResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    operation_id: str
    amount: Decimal
    currency: str
    description: str | None
    status: OperationStatus
    provider_payment_id: str | None


class EventResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )

    event_id: int = Field(
        validation_alias="sequence_no",
    )

    type: str
    from_status: OperationStatus | None
    to_status: OperationStatus
    message: str
    occurred_at: datetime