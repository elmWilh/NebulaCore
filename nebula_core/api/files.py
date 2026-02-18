# nebula_core/api/files.py
# Copyright (c) 2026 Monolink Systems
# Licensed under AGPLv3 (Nebula Open Source Edition, non-corporate)
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from ..core.context import context
from .security import verify_staff_or_internal

router = APIRouter(prefix="/files", tags=["Files"])

class FileContent(BaseModel):
    content: str

@router.get("/{path:path}")
async def read_file(path: str, _=Depends(verify_staff_or_internal)):
    try:
        content = await context.runtime.get_service("file_service").read_file(path)
        return {"path": path, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File {path} not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Access denied for {path}")

@router.post("/{path:path}")
async def write_file(path: str, file: FileContent, _=Depends(verify_staff_or_internal)):
    try:
        await context.runtime.get_service("file_service").write_file(path, file.content)
        return {"status": "ok", "path": path}
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Access denied for {path}")

@router.delete("/{path:path}")
async def delete_file(path: str, _=Depends(verify_staff_or_internal)):
    try:
        await context.runtime.get_service("file_service").delete_file(path)
        return {"status": "deleted", "path": path}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File {path} not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Access denied for {path}")

@router.get("/dir/{path:path}")
async def list_directory(path: str = "", _=Depends(verify_staff_or_internal)):
    try:
        files = await context.runtime.get_service("file_service").list_dir(path)
        return {"path": path, "files": files}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Directory {path} not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Access denied for {path}")

@router.post("/dir/{path:path}")
async def create_directory(path: str, _=Depends(verify_staff_or_internal)):
    try:
        await context.runtime.get_service("file_service").make_dir(path)
        return {"status": "ok", "path": path}
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Access denied for {path}")

@router.delete("/dir/{path:path}")
async def delete_directory(path: str, _=Depends(verify_staff_or_internal)):
    try:
        await context.runtime.get_service("file_service").delete_dir(path)
        return {"status": "deleted", "path": path}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Directory {path} not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Access denied for {path}")
