import asyncio

from selects.server.ws import ProgressBus


async def test_progress_bus_publishes():
    # Use a fresh instance (not the module-level singleton) to avoid cross-test pollution
    bus = ProgressBus()
    received = []

    async def consume():
        async for msg in bus.subscribe():
            received.append(msg)
            break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await bus.publish({"stage": "index", "current": 1, "total": 10})
    await asyncio.wait_for(task, timeout=1.0)
    assert received[0]["stage"] == "index"
