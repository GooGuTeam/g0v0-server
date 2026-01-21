import hashlib
from typing import Annotated

from app.database.user import UserProfileCover
from app.dependencies.cache import UserCacheService
from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.utils import check_image

from .router import router

from app.models.error import ErrorType, RequestError
from fastapi import File


@router.post("/cover/upload", name="上传头图", tags=["用户", "g0v0 API"])
async def upload_cover(
    session: Database,
    content: Annotated[bytes, File(...)],
    current_user: ClientUser,
    storage: StorageService,
    cache_service: UserCacheService,
):
    """上传用户头图

    接收图片数据，验证图片格式和大小后存储到存储服务，并更新用户的头图 URL

    限制条件:
    - 支持的图片格式: PNG、JPEG、GIF
    - 最大文件大小: 10MB
    - 最大图片尺寸: 3000x2000 像素

    返回:
    - 头图 URL 和文件哈希值
    """
    if await current_user.is_restricted(session):
        raise RequestError(ErrorType.ACCOUNT_RESTRICTED)

    # check file
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
