"""Per-IP token-bucket rate limiter for the anonymous group-join endpoint.

Sprint 2.11 / gate #5 of the seven-gate join contract. Pure in-memory
(single-instance Cloud Run is the target for v1; multi-instance needs
Redis or sticky sessions — flagged in the design's "Open Questions").

Design choice: token bucket over fixed window. With a fixed window a
caller can fire ``capacity`` requests just before t=60 and another
``capacity`` immediately after — 2x capacity in a few seconds. The
token bucket smooths refill so 11/60s is genuinely 11/60s.

Time is injected via ``time_provider`` so tests can advance the clock
without ``time.sleep`` — the bucket math is pure.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when the caller's IP has spent all its tokens.

    ``retry_after_seconds`` is the floor of seconds until at least one
    token is back — useful for the HTTP 429 ``Retry-After`` header AND
    the frontend countdown UI.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, retry_after_seconds)
        super().__init__(f"rate limit exceeded; retry after {self.retry_after_seconds}s")


@dataclass
class _Bucket:
    """Per-IP state: token count + last-refill timestamp.

    `tokens` is a float because refill is proportional; we floor-down
    to int when checking ``>= 1`` so the consumer never sees a half-
    token win.
    """

    tokens: float
    last_refill_ts: float


@dataclass
class TokenBucketRateLimiter:
    """Per-IP token bucket.

    Defaults match the design contract: 10 tokens / 60-second window
    (refill rate = 1 token / 6s).

    Thread-safety: not thread-safe by design. FastAPI's single-process
    asyncio model + the GIL is sufficient for the dict mutations here;
    the dict read+write window is microseconds. If you ever switch to
    multi-process (workers > 1), use an external store.
    """

    capacity: int = 10
    refill_seconds: float = 60.0
    time_provider: Callable[[], float] = field(default_factory=lambda: time.monotonic)
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def check(self, key: str) -> None:
        """Spend one token for ``key`` or raise RateLimitExceeded.

        ``key`` is the caller IP (or any string identifier the route
        chooses to bucket on). Pure side-effect: mutates the bucket
        on a successful consume.
        """
        now = self.time_provider()
        bucket = self._buckets.get(key)

        if bucket is None:
            # First request from this key — start with a full bucket
            # minus the one token this call consumes.
            self._buckets[key] = _Bucket(tokens=float(self.capacity - 1), last_refill_ts=now)
            return

        # Refill: tokens added since last touch, capped at capacity.
        elapsed = now - bucket.last_refill_ts
        if elapsed > 0:
            refill_rate = self.capacity / self.refill_seconds  # tokens/sec
            refilled = bucket.tokens + (elapsed * refill_rate)
            bucket.tokens = min(float(self.capacity), refilled)
            bucket.last_refill_ts = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return

        # No tokens — compute retry-after as the time until ONE token
        # is back. refill_rate = capacity / refill_seconds.
        seconds_per_token = self.refill_seconds / self.capacity
        deficit = 1.0 - bucket.tokens
        retry_after = math.ceil(deficit * seconds_per_token)
        raise RateLimitExceeded(retry_after_seconds=retry_after)

    def reset(self, key: str) -> None:
        """Drop a key's bucket — used by tests + revoke flows."""
        self._buckets.pop(key, None)

    def reset_all(self) -> None:
        """Drop every bucket — used by tests + ``AnonymousGroupAuth.reset_for_tests``."""
        self._buckets.clear()


__all__ = [
    "RateLimitExceeded",
    "TokenBucketRateLimiter",
]
