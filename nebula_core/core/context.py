# nebula_core/context.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from dataclasses import dataclass
from typing import Optional
from ..utils.config import settings
from ..utils.logger import get_logger

@dataclass
class NebulaContext:
    config: any
    logger: any
    event_bus: Optional[object] = None
    runtime: Optional[object] = None
    plugin_manager: Optional[object] = None

context = NebulaContext(
    config=settings,
    logger=get_logger("nebula_core")
)
