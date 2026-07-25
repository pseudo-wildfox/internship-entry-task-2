from dataclasses import dataclass
from decimal import Decimal

import httpx


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

        response.raise_for_status()

        data = response.json()

        return ProviderPayment(
            provider_payment_id=data["providerPaymentId"],
            status=data["status"],
        )


class ProviderNetworkError:
    pass


class ProviderTemporaryError:
    pass