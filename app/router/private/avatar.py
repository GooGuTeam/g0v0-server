import hashlib
from typing import Annotated

from app.dependencies.database import Database
from app.dependencies.storage import StorageService
from app.dependencies.user import ClientUser
from app.utils import check_image

from .router import router

from fastapi import File, HTTPException


@router.post("/avatar/upload", name="上传头像", tags=["用户", "g0v0 API"])
async def upload_avatar(
    session: Database,
    content: Annotated[bytes, File(...)],
    current_user: ClientUser,
    storage: StorageService,
):
    """上传用户头像

    接收图片数据，验证图片格式和大小后存储到存储服务，并更新用户的头像 URL

    限制条件:
    - 支持的图片格式: PNG、JPEG、GIF
    - 最大文件大小: 5MB
    - 最大图片尺寸: 256x256 像素

    返回:
    - 头像 URL 和文件哈希值
    """
    if await current_user.is_restricted(session):
        raise HTTPException(status_code=403, detail="Your account is restricted and cannot perform this action.")

    # check file
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
    await session.commit()

    return {
        "url": url,
        "filehash": filehash,
    }
