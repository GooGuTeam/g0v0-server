"""
会话验证路由 - 实现类似 osu! 的邮件验证流程 (API v2)
"""

from __future__ import annotations

from typing import Annotated, Literal

from app.auth import check_totp_backup_code, verify_totp_key
from app.config import settings
from app.const import BACKUP_CODE_LENGTH
from app.database import User
from app.database.auth import TotpKeys
from app.dependencies import get_current_user
from app.dependencies.api_version import APIVersion
from app.dependencies.database import Database, get_redis
from app.dependencies.geoip import get_client_ip
from app.log import logger
from app.service.login_log_service import LoginLogService
from app.service.verification_service import (
    EmailVerificationService,
    LoginSessionService,
)

from .router import router

from fastapi import Depends, Form, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from redis.asyncio import Redis


class VerifyMethod(BaseModel):
    method: Literal["totp", "mail"] = "mail"


class SessionReissueResponse(BaseModel):
    """重新发送验证码响应"""

    success: bool
    message: str


class VerifyFailed(Exception): ...


@router.post(
    "/session/verify",
    name="验证会话",
    description="验证邮件验证码并完成会话认证",
    status_code=204,
    tags=["验证"],
    responses={
        401: {"model": VerifyMethod, "description": "验证失败，返回当前使用的验证方法"},
        204: {"description": "验证成功，无内容返回"},
    },
)
async def verify_session(
    request: Request,
    db: Database,
    api_version: APIVersion,
    redis: Annotated[Redis, Depends(get_redis)],
    verification_key: str = Form(..., description="8 位邮件验证码或者 6 位 TOTP 代码或 10 位备份码 （g0v0 扩展支持）"),
    current_user: User = Security(get_current_user),
) -> Response:
    user_id = current_user.id

    if not await LoginSessionService.check_is_need_verification(db, user_id=user_id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    verify_method: str | None = (
        "mail" if api_version < 20250913 else await LoginSessionService.get_login_method(user_id, redis)
    )

    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "Unknown")
    login_method = "password"

    try:
        totp_key: TotpKeys | None = await current_user.awaitable_attrs.totp_key
        if verify_method is None:
            verify_method = "totp" if totp_key else "mail"
            await LoginSessionService.set_login_method(user_id, verify_method, redis)
        login_method = verify_method

        if verify_method == "totp":
            if not totp_key:
                if settings.enable_email_verification:
                    await LoginSessionService.set_login_method(user_id, "mail", redis)
                    await EmailVerificationService.send_verification_email(
                        db, redis, user_id, current_user.username, current_user.email, ip_address, user_agent
                    )
                    verify_method = "mail"
                    raise VerifyFailed("用户未设置 TOTP，已发送邮件验证码")
                # 如果未开启邮箱验证，则直接认为认证通过
                # 正常不会进入到这里

            elif verify_totp_key(totp_key.secret, verification_key):
                pass
            elif len(verification_key) == BACKUP_CODE_LENGTH and check_totp_backup_code(totp_key, verification_key):
                login_method = "totp_backup_code"
            else:
                raise VerifyFailed("TOTP 验证失败")
        else:
            success, message = await EmailVerificationService.verify_email_code(db, redis, user_id, verification_key)
            if not success:
                raise VerifyFailed(f"邮件验证失败: {message}")

        await LoginLogService.record_login(
            db=db,
            user_id=user_id,
            request=request,
            login_method=login_method,
            login_success=True,
            notes=f"{login_method} 验证成功",
        )
        await LoginSessionService.mark_session_verified(db, redis, user_id)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except VerifyFailed as e:
        await LoginLogService.record_failed_login(
            db=db,
            request=request,
            attempted_username=current_user.username,
            login_method=login_method,
            notes=str(e),
        )
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"method": verify_method})


@router.post(
    "/session/verify/reissue",
    name="重新发送验证码",
    description="重新发送邮件验证码",
    response_model=SessionReissueResponse,
    tags=["验证"],
)
async def reissue_verification_code(
    request: Request,
    db: Database,
    redis: Annotated[Redis, Depends(get_redis)],
    current_user: User = Security(get_current_user),
) -> SessionReissueResponse:
    try:
        ip_address = get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "Unknown")
        user_id = current_user.id
        success, message = await EmailVerificationService.resend_verification_code(
            db,
            redis,
            user_id,
            current_user.username,
            current_user.email,
            ip_address,
            user_agent,
        )

        return SessionReissueResponse(success=success, message=message)

    except ValueError:
        return SessionReissueResponse(success=False, message="无效的用户会话")
    except Exception:
        return SessionReissueResponse(success=False, message="重新发送过程中发生错误")


@router.post(
    "/session/verify/mail-fallback",
    name="邮件验证码回退",
    description="当 TOTP 验证不可用时，使用邮件验证码进行回退验证",
    response_model=VerifyMethod,
    tags=["验证"],
)
async def fallback_email(
    db: Database,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    current_user: User = Security(get_current_user),
) -> VerifyMethod:
    if not await LoginSessionService.get_login_method(current_user.id, redis):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前会话不需要回退")

    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "Unknown")

    await LoginSessionService.set_login_method(current_user.id, "mail", redis)
    success, message = await EmailVerificationService.resend_verification_code(
        db,
        redis,
        current_user.id,
        current_user.username,
        current_user.email,
        ip_address,
        user_agent,
    )
    if not success:
        logger.error(f"[Email Fallback] Failed to send fallback email to user {current_user.id}: {message}")
    return VerifyMethod()
