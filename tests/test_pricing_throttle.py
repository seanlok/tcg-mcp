"""Tests for the throttle helper (token bucket + retry decorator)."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from tcg_mcp.pricing.throttle import TokenBucket, with_retries


async def test_token_bucket_allows_burst_up_to_capacity() -> None:
    tb = TokenBucket(capacity=3, refill_rate=10.0)
    start = time.monotonic()
    # First three should be ~instant (burst).
    for _ in range(3):
        await tb.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


async def test_token_bucket_throttles_after_burst() -> None:
    # Capacity 1, refill 10/sec → 4th call should wait ~0.1s after burst.
    tb = TokenBucket(capacity=1, refill_rate=10.0)
    await tb.acquire()  # consume the initial token
    start = time.monotonic()
    await tb.acquire()  # this one must wait for refill
    elapsed = time.monotonic() - start
    # Should sleep ~0.1s; allow generous margin for CI variance.
    assert 0.05 <= elapsed <= 0.5


def test_token_bucket_validates_args() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_rate=1.0)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_rate=0.0)


async def test_with_retries_returns_immediately_on_success() -> None:
    calls = 0

    async def f() -> int:
        nonlocal calls
        calls += 1
        return 42

    out = await with_retries(f, max_attempts=4, base_delay=0.01)
    assert out == 42
    assert calls == 1


async def test_with_retries_retries_429_and_succeeds() -> None:
    calls = 0

    async def f() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            req = httpx.Request("GET", "https://example.test/")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("rate", request=req, response=resp)
        return "ok"

    out = await with_retries(f, max_attempts=5, base_delay=0.001)
    assert out == "ok"
    assert calls == 3


async def test_with_retries_does_not_retry_400() -> None:
    calls = 0

    async def f() -> None:
        nonlocal calls
        calls += 1
        req = httpx.Request("GET", "https://example.test/")
        resp = httpx.Response(400, request=req)
        raise httpx.HTTPStatusError("bad request", request=req, response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retries(f, max_attempts=4, base_delay=0.001)
    # 400 isn't retriable — we should bail after the first attempt.
    assert calls == 1


async def test_with_retries_gives_up_after_max_attempts() -> None:
    calls = 0

    async def f() -> None:
        nonlocal calls
        calls += 1
        req = httpx.Request("GET", "https://example.test/")
        resp = httpx.Response(503, request=req)
        raise httpx.HTTPStatusError("down", request=req, response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retries(f, max_attempts=3, base_delay=0.001)
    assert calls == 3


async def test_with_retries_handles_timeout() -> None:
    calls = 0

    async def f() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise httpx.ReadTimeout("slow", request=httpx.Request("GET", "https://x"))
        return "ok"

    out = await with_retries(f, max_attempts=3, base_delay=0.001)
    assert out == "ok"
    assert calls == 2


async def test_token_bucket_is_concurrency_safe() -> None:
    tb = TokenBucket(capacity=2, refill_rate=20.0)

    async def take() -> None:
        await tb.acquire()

    # Fire 6 concurrent acquires — should serialize cleanly without crashing.
    await asyncio.gather(*(take() for _ in range(6)))
