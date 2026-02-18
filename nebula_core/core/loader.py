# nebula_core/core/loader.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
import importlib
import inspect
import logging
import pkgutil
from types import ModuleType
from typing import List

MODULES_PACKAGE = "nebula_core.modules"

logger = logging.getLogger("nebula_core.loader")


def discover_modules() -> List[str]:
    """Return list of module names under nebula_core.modules package."""
    try:
        package = importlib.import_module(MODULES_PACKAGE)
    except ModuleNotFoundError:
        logger.debug("Modules package not found: %s", MODULES_PACKAGE)
        return []

    prefix = package.__name__ + "."
    found = [name for _, name, _ in pkgutil.iter_modules(package.__path__, prefix)]
    logger.debug("Discovered modules: %s", found)
    return found


def load_module(module_name: str) -> ModuleType | None:
    """Import module by full name and return module object, or None on error."""
    try:
        mod = importlib.import_module(module_name)
        logger.info("Loaded module: %s", module_name)
        return mod
    except Exception as e:
        logger.exception("Failed to import module %s: %s", module_name, e)
        return None


async def register_modules(event_bus):
    """Discover modules and call register(event_bus) if present."""
    module_names = discover_modules()
    for name in module_names:
        mod = load_module(name)
        if not mod:
            continue
        register = getattr(mod, "register", None)
        if callable(register):
            try:
                # if register is async, await it; else call normally
                if inspect.iscoroutinefunction(register):
                    await register(event_bus)
                else:
                    register(event_bus)
                logger.info("Registered module: %s", name)
            except Exception as e:
                logger.exception("Error registering module %s: %s", name, e)
        else:
            logger.debug("Module %s has no register(event_bus) entrypoint", name)
