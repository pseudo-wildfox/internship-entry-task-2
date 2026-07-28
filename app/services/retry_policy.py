import random
from datetime import datetime, timedelta, timezone


class RetryPolicy:
    """
    Calculates the next retry time using exponential backoff
    with full jitter.

    Retry delays follow this pattern:

        attempt 1 -> random(0, base_delay)
        attempt 2 -> random(0, base_delay * 2)
        attempt 3 -> random(0, base_delay * 4)
        ...

    The delay is capped at max_delay.

    Full jitter prevents multiple retrying workers from
    synchronizing their requests after the same failure.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> None:
        if base_delay <= 0:
            raise ValueError(
                "Base delay must be greater than zero"
            )

        if max_delay <= 0:
            raise ValueError(
                "Max delay must be greater than zero"
            )

        if base_delay > max_delay:
            raise ValueError(
                "Base delay cannot be greater than max delay"
            )

        self._base_delay = base_delay
        self._max_delay = max_delay

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

        delay = random.uniform(
            0,
            exponential_delay,
        )

        return now + timedelta(seconds=delay)