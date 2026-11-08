import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator

QUEUE_SIZE = 100


class EventBus:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)

    def publish(self, shop_id: str, event: dict) -> None:
        for queue in list(self._queues.get(shop_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, shop_id: str) -> AsyncIterator[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues[shop_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues[shop_id].discard(queue)
            if not self._queues[shop_id]:
                del self._queues[shop_id]
