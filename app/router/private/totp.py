from __future__ import annotations

from app.auth import (
    check_totp_backup_code,
    finish_create_totp_key,
    start_create_totp_key,
    totp_redis_key,
    verify_totp_key,
)
from app.config import settings
from app.const import BACKUP_CODE_LENGTH
from app.database.auth import TotpKeys
from app.database.lazer_user import User
from app.dependencies.database import Database, get_redis
from app.dependencies.user import get_client_user
from app.models.totp import FinishStatus, StartCreateTotpKeyResp

from .router import router

from fastapi import Body, Depends, HTTPException, Security
import pyotp
from redis.asyncio import Redis


@router.post(
    "/totp/create",
    name="开始 TOTP 创建流程",
    description=(
        "开始 TOTP 创建流程\n\n"
        "返回 TOTP 密钥和 URI，供用户在身份验证器应用中添加账户。\n\n"
        "然后将身份验证器应用提供的 TOTP 代码请求 PUT `/api/private/totp/create` 来完成 TOTP 创建流程。\n\n"
        "若 5 分钟内未完成或错误 3 次以上则创建流程需要重新开始。"
    ),
    tags=["验证", "g0v0 API"],
    response_model=StartCreateTotpKeyResp,
    status_code=201,
)
async def start_create_totp(
    redis: Redis = Depends(get_redis),
    current_user: User = Security(get_client_user),
):
    if await current_user.awaitable_attrs.totp_key:
        raise HTTPException(status_code=400, detail="TOTP is already enabled for this user")

    previous = await redis.hgetall(totp_redis_key(current_user))  # pyright: ignore[reportGeneralTypeIssues]
    if previous:  # pyright: ignore[reportGeneralTypeIssues]
        return StartCreateTotpKeyResp(
            secret=previous["secret"],
            uri=pyotp.totp.TOTP(previous["secret"]).provisioning_uri(
                name=current_user.email,
                issuer_name=settings.totp_issuer,
            ),
        )
    return await start_create_totp_key(current_user, redis)


@router.put(
    "/totp/create",
    name="完成 TOTP 创建流程",
    description=(
        "完成 TOTP 创建流程，验证用户提供的 TOTP 代码。\n\n"
        "- 如果验证成功，启用用户的 TOTP 双因素验证，并返回备份码。\n- 如果验证失败，返回错误信息。"
    ),
    tags=["验证", "g0v0 API"],
    response_model=list[str],
    status_code=201,
)
async def finish_create_totp(
    session: Database,
    code: str = Body(..., embed=True, description="用户提供的 TOTP 代码"),
    redis: Redis = Depends(get_redis),
    current_user: User = Security(get_client_user),
):
    status, backup_codes = await finish_create_totp_key(current_user, code, redis, session)
    if status == FinishStatus.SUCCESS:
        return backup_codes
    elif status == FinishStatus.INVALID:
        raise HTTPException(status_code=400, detail="No TOTP setup in progress or invalid data")
    elif status == FinishStatus.TOO_MANY_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Too many failed attempts. Please start over.")
    else:
        raise HTTPException(status_code=400, detail="Invalid TOTP code")


@router.delete(
    "/totp",
    name="禁用 TOTP 双因素验证",
    description="禁用当前用户的 TOTP 双因素验证",
    tags=["验证", "g0v0 API"],
    status_code=204,
)
async def disable_totp(
    session: Database,
    code: str = Body(..., embed=True, description="用户提供的 TOTP 代码或备份码"),
    current_user: User = Security(get_client_user),
):
    totp = await session.get(TotpKeys, current_user.id)
    if not totp:
        raise HTTPException(status_code=400, detail="TOTP is not enabled for this user")
    if verify_totp_key(totp.secret, code) or (len(code) == BACKUP_CODE_LENGTH and check_totp_backup_code(totp, code)):
        await session.delete(totp)
        await session.commit()
    else:
        raise HTTPException(status_code=400, detail="Invalid TOTP code or backup code")
