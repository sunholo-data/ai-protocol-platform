"""Unit tests for ``auth.group_rate_limit`` (sprint 2.11, M1).

Per-IP token bucket — covers the rate-limit gate (#5 in the design's
seven-gate join contract). The bucket math is pure (no I/O); these
tests run the clock manually via ``time_provider`` injection so we
can verify refill semantics without ``time.sleep``.

Covered:
- N joins from one IP succeed; N+1 in the same 60s window fails
- Bucket refills proportionally over time
- Different IPs have isolated buckets
- Refill cap: a long-idle IP gets the full capacity back, not more
- Configurable limit + window
"""

from __future__ import annotations

import pytest

from auth.group_rate_limit import RateLimitExceeded, TokenBucketRateLimiter


def test_default_limit_is_ten_per_minute():
    """Design contract: default 10 joins/min/IP."""
    limiter = TokenBucketRateLimiter()
    assert limiter.capacity == 10
    assert limiter.refill_seconds == 60


def test_n_joins_within_limit_succeed():
    """10 consecutive joins from same IP within window all pass."""
    limiter = TokenBucketRateLimiter(time_provider=lambda: 1000.0)
    for _ in range(10):
        limiter.check("203.0.113.42")
    # 11th in same instant must fail.
    with pytest.raises(RateLimitExceeded):
        limiter.check("203.0.113.42")


def test_different_ips_have_isolated_buckets():
    """Bucket key is the IP — one IP exhausting doesn't affect another."""
    limiter = TokenBucketRateLimiter(time_provider=lambda: 1000.0)
    for _ in range(10):
        limiter.check("1.1.1.1")
    # Same instant; different IP still has full bucket.
    for _ in range(10):
        limiter.check("2.2.2.2")
    # Both now at zero.
    with pytest.raises(RateLimitExceeded):
        limiter.check("1.1.1.1")
    with pytest.raises(RateLimitExceeded):
        limiter.check("2.2.2.2")


def test_bucket_refills_proportionally():
    """After 30s the IP has 5 tokens back (60s window, 10-capacity)."""
    now = [1000.0]
    limiter = TokenBucketRateLimiter(time_provider=lambda: now[0])
    for _ in range(10):
        limiter.check("3.3.3.3")
    with pytest.raises(RateLimitExceeded):
        limiter.check("3.3.3.3")

    # Advance 30s — half the window — 5 tokens should be back.
    now[0] += 30.0
    for _ in range(5):
        limiter.check("3.3.3.3")
    with pytest.raises(RateLimitExceeded):
        limiter.check("3.3.3.3")


def test_bucket_refill_caps_at_capacity():
    """An idle IP doesn't accumulate beyond the configured capacity."""
    now = [1000.0]
    limiter = TokenBucketRateLimiter(time_provider=lambda: now[0])
    # Consume some tokens (5 of 10 capacity).
    for _ in range(5):
        limiter.check("4.4.4.4")
    # Skip ahead 24 hours — refill would theoretically be huge.
    now[0] += 24 * 3600
    # Should still cap at 10 — 11th fails.
    for _ in range(10):
        limiter.check("4.4.4.4")
    with pytest.raises(RateLimitExceeded):
        limiter.check("4.4.4.4")


def test_configurable_capacity_and_window():
    """Forks can tighten or loosen the limit at construction time."""
    now = [1000.0]
    limiter = TokenBucketRateLimiter(capacity=3, refill_seconds=10, time_provider=lambda: now[0])
    for _ in range(3):
        limiter.check("5.5.5.5")
    with pytest.raises(RateLimitExceeded):
        limiter.check("5.5.5.5")
    now[0] += 10.0
    # Full refill after one window.
    for _ in range(3):
        limiter.check("5.5.5.5")


def test_rate_limit_exceeded_carries_retry_hint():
    """RateLimitExceeded includes ``retry_after_seconds`` for the caller
    to surface in the 429 response. Useful for the frontend countdown UI."""
    now = [1000.0]
    limiter = TokenBucketRateLimiter(time_provider=lambda: now[0])
    for _ in range(10):
        limiter.check("6.6.6.6")
    with pytest.raises(RateLimitExceeded) as exc_info:
        limiter.check("6.6.6.6")
    err = exc_info.value
    # At capacity = 10, refill_seconds = 60, refill_rate is 1 token / 6s.
    # So caller should retry in ~6s.
    assert 1 <= err.retry_after_seconds <= 60
