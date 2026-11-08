from awning.ratelimit import RateLimiter


def test_rate_limiter_window() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 10, clock=lambda: now[0])
    assert limiter.allow("a")
    assert limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 10.5
    assert limiter.allow("a")
