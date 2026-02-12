# nebula_core/core/events.py
import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List, Tuple

Listener = Callable[[Any], Awaitable[None]]  # async callable that accepts payload


class EventBus:
    def __init__(self, logger: logging.Logger = None):
        self._listeners: Dict[str, List[Tuple[Listener, bool]]] = {}
        # tuple(listener, once_flag)
        self._lock = asyncio.Lock()
        self._logger = logger or logging.getLogger("nebula_core.events")

    async def subscribe(self, event_name: str, listener: Listener, *, once: bool = False):
        """Register async listener for event_name. If once=True, listener is removed after first call."""
        async with self._lock:
            self._listeners.setdefault(event_name, []).append((listener, once))
            self._logger.debug("Listener subscribed to %s (once=%s)", event_name, once)

    async def on(self, event_name: str, listener: Listener, *, once: bool = False):
        """Compatibility alias for subscribe()."""
        await self.subscribe(event_name, listener, once=once)

    async def unsubscribe(self, event_name: str, listener: Listener):
        async with self._lock:
            if event_name not in self._listeners:
                return
            before = len(self._listeners[event_name])
            self._listeners[event_name] = [(l, o) for (l, o) in self._listeners[event_name] if l != listener]
            after = len(self._listeners[event_name])
            self._logger.debug("Listener unsubscribed from %s (%d -> %d)", event_name, before, after)

    async def emit(self, event_name: str, payload: Any = None):
        """Emit event asynchronously to all listeners. Errors in listeners are caught and logged."""
        # copy listeners to avoid race issues if listeners mutate during emit
        async with self._lock:
            listeners = list(self._listeners.get(event_name, []))
        if not listeners:
            self._logger.debug("No listeners for event: %s", event_name)
            return

        async def _call_listener(lst, pl, ev, once_flag):
            try:
                result = lst(pl)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                self._logger.exception("Error in listener for %s: %s", ev, e)
            finally:
                if once_flag:
                    await self.unsubscribe(ev, lst)

        await asyncio.gather(
            *(_call_listener(listener, payload, event_name, once) for (listener, once) in listeners)
        )

    async def clear(self, event_name: str):
        async with self._lock:
            if event_name in self._listeners:
                del self._listeners[event_name]
                self._logger.debug("Cleared listeners for %s", event_name)
