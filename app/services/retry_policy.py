import random
from datetime import datetime, timedelta, timezone


class RetryPolicy:
    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: float = 0.2,
    ) -> None:
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._jitter = jitter

    def next_retry_at(
        self,
        *,
        attempt: int,
        now: datetime | None = None,
    ) -> datetime:
        if attempt < 1:
            raise ValueError(
                "Attempt must be greater than zero"
            )

        if now is None:
            now = datetime.now(timezone.utc)

        exponential_delay = min(
            self._base_delay * (2 ** (attempt - 1)),
            self._max_delay,
        )

        jitter = random.uniform(
            0,
            exponential_delay * self._jitter,
        )

        delay = min(
            exponential_delay + jitter,
            self._max_delay,
        )

        return now + timedelta(seconds=delay)