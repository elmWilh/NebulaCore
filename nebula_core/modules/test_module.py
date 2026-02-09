# nebula_core/modules/test_module.py
import asyncio
import logging
from typing import Any

logger = logging.getLogger("nebula_core.test_module")


async def on_startup(payload: Any):
    logger.info("test_module received system.startup: %s", payload)


async def periodic_heartbeat(event_bus):
    """A periodic task that sends events every 5 seconds."""
    i = 0
    while True:
        await asyncio.sleep(5)
        i += 1
        logger.info("test_module heartbeat %d", i)
        await event_bus.emit("module.test.heartbeat", {"count": i})



def register(event_bus):
    """
    The module entry point is called by the loader.
    This is where we subscribe to events and start background tasks.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    async def _register_async():
        await event_bus.subscribe("system.startup", on_startup)
        async def on_heartbeat(payload):
            logger.debug("heartbeat event got: %s", payload)
            await event_bus.subscribe("module.test.heartbeat", on_heartbeat)

        asyncio.create_task(periodic_heartbeat(event_bus))

    if loop and loop.is_running():
        asyncio.create_task(_register_async())
    else:
        asyncio.run(_register_async())
