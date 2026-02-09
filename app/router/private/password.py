from typing import Annotated

from app.auth import (
    authenticate_user,
    check_totp_backup_code,
    get_password_hash,
    validate_password,
    verify_totp_key_with_replay_protection,
)
from app.const import BACKUP_CODE_LENGTH
from app.database.auth import OAuthToken, TotpKeys
from app.database.verification import LoginSession, TrustedDevice
from app.dependencies.database import Database, Redis
from app.dependencies.user import ClientUser
from app.log import log
from app.models.error import ErrorType, FieldMissingError, RequestError

from .router import router

from fastapi import Depends, Form
from fastapi_limiter.depends import RateLimiter
from pyrate_limiter import Duration, Limiter, Rate
from sqlmodel import col, delete

logger = log("Auth")


@router.post(
    "/password/change",
    name="更改密码",
    tags=["验证", "g0v0 API"],
    status_code=204,
    dependencies=[Depends(RateLimiter(limiter=Limiter(Rate(3, Duration.MINUTE * 5))))],
)
async def change_password(
    current_user: ClientUser,
    session: Database,
    redis: Redis,
    new_password: Annotated[str, Form(description="新密码")],
    current_password: Annotated[str | None, Form(description="当前密码（未启用TOTP时必填）")] = None,
    totp_code: Annotated[str | None, Form(description="TOTP验证码或备份码（已启用TOTP时必填）")] = None,
):
    """更改用户密码

    验证方式：
    - 如果用户已启用TOTP，必须提供 totp_code（6位数字验证码或备份码），优先验证TOTP
    - 如果用户未启用TOTP，必须提供 current_password 进行密码验证

    同时删除所有的已登录会话和信任设备

    速率限制: 5 分钟内最多 3 次
    """
    # 验证新密码格式
    if errors := validate_password(new_password):
        raise RequestError(ErrorType.INVALID_PASSWORD, {"errors": errors}, status_code=400)

    # 检查用户是否启用了TOTP
    totp_key = await session.get(TotpKeys, current_user.id)

    if totp_key:
        # 用户已启用TOTP，必须验证TOTP
        if not totp_code:
            raise RequestError(ErrorType.TOTP_CODE_REQUIRED)

        is_verified = False
        if len(totp_code) == 6 and totp_code.isdigit():
            is_verified = await verify_totp_key_with_replay_protection(
                current_user.id, totp_key.secret, totp_code, redis
            )
        elif len(totp_code) == BACKUP_CODE_LENGTH:
            is_verified = check_totp_backup_code(totp_key, totp_code)
            if is_verified:
                session.add(totp_key)
        else:
            raise RequestError(ErrorType.INVALID_TOTP_FORMAT, {"args": BACKUP_CODE_LENGTH})

        if not is_verified:
            raise RequestError(ErrorType.INVALID_TOTP_OR_BACKUP_CODE)

        logger.info(f"User {current_user.id} verified identity with TOTP for password change")

    else:
        # 用户未启用TOTP，必须验证当前密码
        if not current_password:
            raise FieldMissingError(["current_password"])

        if not await authenticate_user(session, current_user.username, current_password):
            raise RequestError(ErrorType.PASSWORD_INCORRECT)

        logger.info(f"User {current_user.id} verified identity with password for password change")

    user_id = current_user.id

    current_user.pw_bcrypt = get_password_hash(new_password)

    await session.execute(delete(TrustedDevice).where(col(TrustedDevice.user_id) == user_id))
    await session.execute(delete(LoginSession).where(col(LoginSession.user_id) == user_id))
    await session.execute(delete(OAuthToken).where(col(OAuthToken.user_id) == user_id))

    await session.commit()
    logger.info(f"User {user_id} successfully changed password, all sessions revoked")
