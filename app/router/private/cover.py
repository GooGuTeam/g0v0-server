"""User profile cover upload endpoint.

Provides API for users to upload and update their profile cover images.
"""

import hashlib
from typing import Annotated

from app.database.user import UserProfileCover
from app.dependencies.cache import UserCacheService
from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.helpers import check_image
from app.models.error import ErrorType, RequestError

from .router import router

from fastapi import File


@router.post(
    "/cover/upload",
    name="Upload cover image",
    tags=["User", "g0v0 API"],
    description="Upload user profile cover image.",
)
async def upload_cover(
    session: Database,
    content: Annotated[bytes, File(...)],
    current_user: ClientUser,
    storage: StorageService,
    cache_service: UserCacheService,
):
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    # Check file
    format_ = check_image(content, 10 * 1024 * 1024, 3000, 2000)

    if url := current_user.cover["url"]:
        path = storage.get_file_name_by_url(url)
        if path:
            await storage.delete_file(path)

    filehash = hashlib.sha256(content).hexdigest()
    storage_path = f"cover/{current_user.id}_{filehash}.png"
    if not await storage.is_exists(storage_path):
        await storage.write_file(storage_path, content, f"image/{format_}")
    url = await storage.get_file_url(storage_path)
    current_user.cover = UserProfileCover(url=url)
    await cache_service.invalidate_user_cache(current_user.id)
    await session.commit()

    return {
        "url": url,
        "filehash": filehash,
    }
