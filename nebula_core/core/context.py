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
