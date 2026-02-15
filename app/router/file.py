"""File serving router for local storage.

This module provides endpoints for serving files from local storage.
Only active when using LocalStorageService backend.
"""

from app.dependencies.storage import StorageService as StorageServiceDep
from app.models.error import ErrorType, RequestError
from app.storage import LocalStorageService

from fastapi import APIRouter
from fastapi.responses import FileResponse

file_router = APIRouter(prefix="/file", include_in_schema=False)


@file_router.get("/{path:path}")
async def get_file(path: str, storage: StorageServiceDep):
    """Serve a file from local storage.

    Only works when LocalStorageService is configured. For other storage
    backends (e.g., S3), clients should access files directly via their URLs.

    Args:
        path: Relative file path within the storage.
        storage: Storage service dependency.

    Returns:
        FileResponse with the requested file.

    Raises:
        RequestError: If file not found or storage is not local.
    """
    if not isinstance(storage, LocalStorageService):
        raise RequestError(ErrorType.NOT_FOUND)
    if not await storage.is_exists(path):
        raise RequestError(ErrorType.NOT_FOUND)

    try:
        return FileResponse(
            path=storage._get_file_path(path),
            media_type="application/octet-stream",
            filename=path.split("/")[-1],
        )
    except FileNotFoundError:
        raise RequestError(ErrorType.NOT_FOUND)
