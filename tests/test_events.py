import asyncio

from awning.events import EventBus


async def test_subscriber_receives_events_for_its_shop() -> None:
    bus = EventBus()
    received: list[dict] = []

    stream = bus.subscribe("shp_1")

    async def listen() -> None:
        async for event in stream:
            received.append(event)
            if len(received) == 2:
                return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    bus.publish("shp_other", {"type": "x"})
    bus.publish("shp_1", {"type": "a"})
    bus.publish("shp_1", {"type": "b"})
    await asyncio.wait_for(task, 1)
    assert [e["type"] for e in received] == ["a", "b"]
    assert "shp_1" in bus._queues
    await stream.aclose()
    assert "shp_1" not in bus._queues


async def test_full_queue_drops_without_blocking() -> None:
    bus = EventBus()
    gen = bus.subscribe("shp_1")
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0)
    for i in range(250):
        bus.publish("shp_1", {"type": "e", "n": i})
    await task
    await gen.aclose()
