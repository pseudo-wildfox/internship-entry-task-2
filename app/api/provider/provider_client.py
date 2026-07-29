from dataclasses import dataclass
from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict


class ProviderClientError(Exception):
    """Base exception for provider communication errors."""


class ProviderHttpError(ProviderClientError):
    """Provider returned an unsuccessful HTTP response."""

    def __init__(
        self,
        *,
        status_code: int,
        response_body: str,
    ) -> None:
        self.status_code = status_code
        self.response_body = response_body

        super().__init__(
            f"Provider returned HTTP {status_code}: "
            f"{response_body}"
        )


class ProviderInvalidResponseError(ProviderClientError):
    """Provider returned an invalid or unexpected response."""


class ProviderPaymentResponse(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
    )

    providerPaymentId: str
    status: str


@dataclass(frozen=True)
class ProviderPayment:
    provider_payment_id: str
    status: str


class ProviderClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        provider_url: str,
    ) -> None:
        self._client = client
        self._provider_url = provider_url.rstrip("/")

    async def create_payment(
        self,
        *,
        operation_id: str,
        amount: Decimal,
        currency: str,
    ) -> ProviderPayment:
        try:
            response = await self._client.post(
                f"{self._provider_url}/payments",
                headers={
                    "Idempotency-Key": operation_id,
                    "X-Correlation-ID": operation_id,
                },
                json={
                    "operationId": operation_id,
                    "amount": f"{amount:.2f}",
                    "currency": currency,
                },
            )

        except httpx.HTTPError as exc:
            # This includes connection errors, timeouts, etc.
            #
            # IMPORTANT:
            # The provider may have already accepted the payment.
            # The caller must retry using the same operation_id.
            raise ProviderClientError(
                "Provider request failed at transport level"
            ) from exc

        if response.status_code != 202:
            raise ProviderHttpError(
                status_code=response.status_code,
                response_body=response.text,
            )

        try:
            data = ProviderPaymentResponse.model_validate(
                response.json(),
            )
        except (ValueError, TypeError) as exc:
            raise ProviderInvalidResponseError(
                "Provider returned invalid payment response"
            ) from exc

        return ProviderPayment(
            provider_payment_id=data.providerPaymentId,
            status=data.status,
        )