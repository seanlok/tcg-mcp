"""Rate limiting + retry helper.

Each pricing provider declares its own throttle policy and the shared
`call` helper enforces it. Two pieces:

1. `TokenBucket` — async, fixed-rate token bucket. `acquire()` blocks until
   a token is available. Used to cap calls per second / per minute.
2. `with_retries()` — async wrapper that retries on httpx 429 and 5xx with
   exponential backoff + jitter.

We avoid pulling `aiolimiter` so this stays a stdlib-only implementation.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

log = logging.getLogger(__name__)

T = TypeVar("T")


class TokenBucket:
    """Simple async token bucket.

    Configure with `capacity` tokens and a `refill_rate` (tokens per second).
    Calling `acquire()` consumes one token, sleeping if none are available.

    Examples:
        # PriceCharting: 1 req/sec
        tb = TokenBucket(capacity=1, refill_rate=1.0)

        # Pokemon TCG API unkeyed: 30 req/min ≈ 0.5 req/sec
        tb = TokenBucket(capacity=2, refill_rate=0.5)
    """

    def __init__(self, capacity: int, refill_rate: float) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be > 0")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens: float = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    async def acquire(self, count: int = 1) -> None:
        """Block until `count` tokens are available."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= count:
                    self._tokens -= count
                    return
                # Need to wait for enough tokens
                deficit = count - self._tokens
                wait = deficit / self.refill_rate
                # Release the lock while sleeping so other waiters can refill check
                # (but we still hold it because asyncio.Lock is non-reentrant; the
                # whole acquire is serialized — fine for our QPS levels).
                await asyncio.sleep(wait)


async def with_retries(
    func: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    provider: str = "?",
) -> T:
    """Run `func` with exponential-backoff retry on 429 / 5xx / network errors.

    Args:
        func: Zero-arg async callable. Build it via a closure when needed.
        max_attempts: Total tries including the first. 4 = 1 try + 3 retries.
        base_delay: Initial delay in seconds.
        max_delay: Cap for the exponential backoff.
        provider: Provider name for log messages.

    Raises:
        Whatever func raises after exhausting retries (most often
        httpx.HTTPStatusError or httpx.RequestError).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await func()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            retriable = status == 429 or 500 <= status < 600
            if not retriable or attempt >= max_attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter up to 25%
            log.warning(
                "[%s] HTTP %s on attempt %d/%d, sleeping %.2fs",
                provider, status, attempt, max_attempts, delay,
            )
            await asyncio.sleep(delay)
        except (httpx.TimeoutException, httpx.RequestError) as e:
            if attempt >= max_attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)
            log.warning(
                "[%s] %s on attempt %d/%d, sleeping %.2fs",
                provider, type(e).__name__, attempt, max_attempts, delay,
            )
            await asyncio.sleep(delay)
