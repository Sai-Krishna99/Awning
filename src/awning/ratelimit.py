import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(self, max_events: int, window_s: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        events = self._events[key]
        while events and events[0] <= now - self.window_s:
            events.popleft()
        if len(events) >= self.max_events:
            return False
        events.append(now)
        return True
