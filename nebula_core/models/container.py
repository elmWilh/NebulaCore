# nebula_core/models/container.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from pydantic import BaseModel
from typing import List, Optional

class ContainerCreate(BaseModel):
    name: str
    image: str
    ram: int  
    cpu: int  
    ports: str 
    env: Optional[str] = None
    restart: bool = True
    users: List[str] = []