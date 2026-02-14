"""User avatar upload endpoint.

Provides API for users to upload and update their profile avatars.
"""

import hashlib
from typing import Annotated

from app.dependencies.cache import UserCacheService
from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError
from app.utils import check_image

from .router import router

from fastapi import File


@router.post("/avatar/upload", name="Upload avatar", tags=["User", "g0v0 API"], description="Upload user avatar.")
async def upload_avatar(
    session: Database,
    content: Annotated[bytes, File(...)],
    current_user: ClientUser,
    storage: StorageService,
    cache_service: UserCacheService,
):
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    # Check file
    format_ = check_image(content, 5 * 1024 * 1024, 256, 256)

    if url := current_user.avatar_url:
        path = storage.get_file_name_by_url(url)
        if path:
            await storage.delete_file(path)

    filehash = hashlib.sha256(content).hexdigest()
    storage_path = f"avatars/{current_user.id}_{filehash}.png"
    if not await storage.is_exists(storage_path):
        await storage.write_file(storage_path, content, f"image/{format_}")
    url = await storage.get_file_url(storage_path)
    current_user.avatar_url = url
    await cache_service.invalidate_user_cache(current_user.id)
    await session.commit()

    return {
        "url": url,
        "filehash": filehash,
    }
