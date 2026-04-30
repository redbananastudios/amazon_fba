"""Token-bucket rate limiter for Keepa API calls.

Configured for the $49/month tier defaults: 20 tokens/minute sustained,
100-token burst. Callers acquire tokens before issuing API calls; the
bucket blocks (sleeps) until enough tokens have refilled.

The `sleep` injection point exists for tests — production uses
`time.sleep`, tests pass a recording stub so they can assert on the
sleep durations without actually pausing.
"""
from __future__ import annotations

import time
from typing import Callable


class TokenBucket:
    """Simple synchronous token bucket.

    Token-per-minute model: `tokens_per_minute` tokens drip in linearly
    (rate = tokens_per_minute / 60 per second), capped at `burst`.
    `acquire(n)` blocks until `n` tokens are available, then deducts them.
    """

    def __init__(
        self,
        tokens_per_minute: float,
        burst: int,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if tokens_per_minute <= 0:
            raise ValueError("tokens_per_minute must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self._refill_rate = tokens_per_minute / 60.0
        self._capacity = burst
        self._available = float(burst)
        self._sleep = sleep if sleep is not None else time.sleep
        self._clock = clock if clock is not None else time.monotonic
        self._last_refill = self._clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._available = min(
                self._capacity, self._available + elapsed * self._refill_rate
            )
            self._last_refill = now

    def acquire(self, tokens: int) -> None:
        """Block until `tokens` are available, then deduct them.

        `tokens=0` returns immediately. Requests larger than the bucket's
        capacity raise — they would otherwise deadlock.
        """
        if tokens <= 0:
            return
        if tokens > self._capacity:
            raise ValueError(
                f"requested {tokens} tokens exceeds bucket capacity {self._capacity}"
            )
        while True:
            self._refill()
            if self._available >= tokens:
                self._available -= tokens
                return
            deficit = tokens - self._available
            wait = deficit / self._refill_rate
            self._sleep(wait)

    def refund(self, tokens: int) -> None:
        """Return `tokens` to the bucket, capped at capacity.

        Used by callers that pre-`acquire` a conservative estimate before an
        API call, then learn the true cost from the response. Refund of a
        non-positive number is a no-op (over-acquired calls don't run in
        reverse — we never *deduct* via this method).
        """
        if tokens <= 0:
            return
        self._refill()
        self._available = min(self._capacity, self._available + tokens)
