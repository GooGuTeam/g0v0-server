from typing import Annotated

from app.auth import (
    check_totp_backup_code,
    finish_create_totp_key,
    start_create_totp_key,
    totp_redis_key,
    verify_totp_key_with_replay_protection,
)
from app.const import BACKUP_CODE_LENGTH
from app.database.auth import TotpKeys
from app.dependencies.database import Database, Redis
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError
from app.models.totp import FinishStatus, StartCreateTotpKeyResp

from .router import router

from fastapi import Body
from pydantic import BaseModel
import pyotp


class TotpStatusResp(BaseModel):
    """TOTP状态响应"""

    enabled: bool
    created_at: str | None = None


@router.get(
    "/totp/status",
    name="检查 TOTP 状态",
    description="检查当前用户是否已启用 TOTP 双因素验证",
    tags=["验证", "g0v0 API"],
    response_model=TotpStatusResp,
)
async def get_totp_status(
    current_user: ClientUser,
):
    """检查用户是否已创建TOTP"""
    totp_key = await current_user.awaitable_attrs.totp_key

    if totp_key:
        return TotpStatusResp(enabled=True, created_at=totp_key.created_at.isoformat())
    else:
        return TotpStatusResp(enabled=False)


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
    redis: Redis,
    current_user: ClientUser,
):
    if await current_user.awaitable_attrs.totp_key:
        raise RequestError(ErrorType.TOTP_ALREADY_ENABLED)

    previous = await redis.hgetall(totp_redis_key(current_user))  # pyright: ignore[reportGeneralTypeIssues]
    if previous:  # pyright: ignore[reportGeneralTypeIssues]
        from app.auth import _generate_totp_account_label, _generate_totp_issuer_name

        account_label = _generate_totp_account_label(current_user)
        issuer_name = _generate_totp_issuer_name()

        return StartCreateTotpKeyResp(
            secret=previous["secret"],
            uri=pyotp.totp.TOTP(previous["secret"]).provisioning_uri(
                name=account_label,
                issuer_name=issuer_name,
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
    code: Annotated[str, Body(..., embed=True, description="用户提供的 TOTP 代码")],
    redis: Redis,
    current_user: ClientUser,
):
    status, backup_codes = await finish_create_totp_key(current_user, code, redis, session)
    if status == FinishStatus.SUCCESS:
        return backup_codes
    elif status == FinishStatus.INVALID:
        raise RequestError(ErrorType.NO_TOTP_SETUP_OR_INVALID_DATA)
    elif status == FinishStatus.TOO_MANY_ATTEMPTS:
        raise RequestError(ErrorType.TOO_MANY_FAILED_ATTEMPTS)
    else:
        raise RequestError(ErrorType.INVALID_TOTP_CODE)


@router.delete(
    "/totp",
    name="禁用 TOTP 双因素验证",
    description="禁用当前用户的 TOTP 双因素验证",
    tags=["验证", "g0v0 API"],
    status_code=204,
)
async def disable_totp(
    session: Database,
    code: Annotated[str, Body(..., embed=True, description="用户提供的 TOTP 代码或备份码")],
    redis: Redis,
    current_user: ClientUser,
):
    totp = await session.get(TotpKeys, current_user.id)
    if not totp:
        raise RequestError(ErrorType.TOTP_NOT_ENABLED)

    # 使用防重放保护的TOTP验证或备份码验证
    is_totp_valid = False
    if len(code) == 6 and code.isdigit():
        is_totp_valid = await verify_totp_key_with_replay_protection(current_user.id, totp.secret, code, redis)
    elif len(code) == BACKUP_CODE_LENGTH:
        is_totp_valid = check_totp_backup_code(totp, code)

    if is_totp_valid:
        await session.delete(totp)
        await session.commit()
    else:
        raise RequestError(ErrorType.INVALID_TOTP_OR_BACKUP_CODE)
