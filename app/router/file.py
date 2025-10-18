from app.dependencies.storage import StorageService as StorageServiceDep
from app.storage import LocalStorageService

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

file_router = APIRouter(prefix="/file", include_in_schema=False)


@file_router.get("/{path:path}")
async def get_file(path: str, storage: StorageServiceDep):
    if not isinstance(storage, LocalStorageService):
        raise HTTPException(404, "Not Found")
    if not await storage.is_exists(path):
        raise HTTPException(404, "Not Found")

    try:
        return FileResponse(
            path=storage._get_file_path(path),
            media_type="application/octet-stream",
            filename=path.split("/")[-1],
        )
    except FileNotFoundError:
        raise HTTPException(404, "Not Found")
