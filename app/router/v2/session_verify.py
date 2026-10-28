"""Session verification API endpoints.

This module implements email/TOTP verification flow similar to osu! for session
authentication (API v2). Supports both email verification codes and TOTP-based
two-factor authentication.
"""

from typing import Annotated, Literal

from app.auth import verify_user_totp
from app.config import settings
from app.const import BACKUP_CODE_LENGTH
from app.dependencies.api_version import APIVersion
from app.dependencies.database import Database, Redis, get_redis
from app.dependencies.geoip import IPAddress
from app.dependencies.rate_limit import create_rate_limiter
from app.dependencies.user import UserAndToken, get_client_user_and_token
from app.dependencies.user_agent import UserAgentInfo
from app.log import log

from pyrate_limiter import Duration, Rate

logger = log("Verification")
from app.models.error import ErrorType, RequestError
from app.service.auth_security import check_verification_limit, release_verification_attempt
from app.service.login_log_service import LoginLogService
from app.service.verification_service import (
    EmailVerificationService,
    LoginSessionService,
    lock_user,
)

from .router import router

from fastapi import Depends, Form, Header, Request, Security, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel


class VerifyMethod(BaseModel):
    """Verification method response.

    Attributes:
        method: The verification method being used ("totp" or "mail").
    """

    method: Literal["totp", "mail"] = "mail"


class SessionReissueResponse(BaseModel):
    """Response for verification code reissue request.

    Attributes:
        success: Whether the reissue was successful.
        message: Description of the result.
    """

    success: bool
    message: str


class VerifyFailedError(Exception):
    """Exception raised when verification fails.

    Attributes:
        reason: Optional detailed reason for the failure.
        should_reissue: Whether a new verification code should be sent.
    """

    def __init__(self, message: str, reason: str | None = None, should_reissue: bool = False):
        super().__init__(message)
        self.reason = reason
        self.should_reissue = should_reissue


@router.post(
    "/session/verify",
    name="Verify session",
    description="Verify email verification code and complete session authentication",
    status_code=204,
    tags=["Verification"],
    responses={
        401: {"model": VerifyMethod, "description": "Verification failed, returns current verification method"},
        204: {"description": "Verification successful, no content returned"},
    },
    dependencies=[
        Depends(create_rate_limiter(Rate(3, Duration.MINUTE * 5), bucket_key="rate-limit:v2:session-verify")),
    ],
)
async def verify_session(
    request: Request,
    db: Database,
    api_version: APIVersion,
    user_agent: UserAgentInfo,
    ip_address: IPAddress,
    redis: Annotated[Redis, Depends(get_redis)],
    verification_key: Annotated[
        str,
        Form(
            ...,
            description=(
                "8-digit email verification code, 6-digit TOTP code, or 10-digit backup code (g0v0 extension)"
            ),
        ),
    ],
    user_and_token: Annotated[UserAndToken, Security(get_client_user_and_token)],
    web_uuid: Annotated[str | None, Header(include_in_schema=False, alias="X-UUID")] = None,
) -> Response:
    """Verify the current session using email code or TOTP.

    Args:
        request: The FastAPI request object.
        db: Database session dependency.
        api_version: API version for method selection.
        user_agent: User agent information.
        ip_address: Client IP address.
        redis: Redis connection dependency.
        verification_key: The verification code/key.
        user_and_token: Tuple of user and auth token.
        web_uuid: Optional web session UUID.

    Returns:
        Response: 204 No Content on success.

    Raises:
        JSONResponse: 401 Unauthorized on verification failure.
    """
    current_user, token = user_and_token
    user_id, token_id = current_user.id, token.id
    await lock_user(db, user_id)
    login_session = await LoginSessionService.require_session(db, user_id, token_id, for_update=True)
    if login_session.is_verified or not (settings.enable_email_verification or settings.enable_totp_verification):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    await check_verification_limit(redis, "session", user_id)
    method = await LoginSessionService.verification_method(db, redis, user_id, token_id, api_version)
    failed_method = method or login_session.verification_method or "mail"
    if method == "mail" and login_session.verification_method == "totp" and len(verification_key) != 8:
        user_name, email, country = current_user.username, current_user.email, current_user.country_code
        await LoginSessionService.set_login_method(user_id, token_id, "mail", redis, db=db)
        await db.commit()
        await EmailVerificationService.send_verification_email(
            db,
            redis,
            user_id,
            user_name,
            email,
            ip_address,
            user_agent,
            country,
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": "User TOTP is unavailable, switched to email verification",
                "method": "mail",
            },
        )
    try:
        if method == "totp":
            if not await verify_user_totp(db, redis, user_id, verification_key):
                if len(verification_key) not in (6, BACKUP_CODE_LENGTH):
                    raise VerifyFailedError("TOTP code length error, should be 6 digits", reason="incorrect_length")
                if not verification_key.isascii() or (len(verification_key) == 6 and not verification_key.isdigit()):
                    raise VerifyFailedError("TOTP code format error, should be digits only", reason="incorrect_format")
                raise VerifyFailedError("TOTP verification failed", reason="incorrect_key")
        elif method == "mail":
            valid, message = await EmailVerificationService.verify_email_code(db, redis, user_id, verification_key)
            if not valid:
                raise VerifyFailedError(message)
        else:
            # A disabled/unavailable factor cannot complete an outstanding challenge.
            raise VerifyFailedError("Verification method is unavailable")
        await LoginSessionService.mark_session_verified(db, redis, user_id, token_id, ip_address, user_agent, web_uuid)
        await LoginLogService.record_login(
            db,
            user_id,
            request,
            user_agent=user_agent.raw_ua,
            login_method=f"{method}_verification",
            commit=False,
        )
        await db.commit()
    except VerifyFailedError as error:
        await db.rollback()
        await LoginLogService.record_login(
            db,
            user_id,
            request,
            user_agent=user_agent.raw_ua,
            login_success=False,
            login_method=f"{method or 'password'}_verification",
            notes=f"verification_failed: {error}",
        )
        content = {"error": "Verification failed", "method": failed_method}
        if error.reason:
            content["reason"] = error.reason
        return JSONResponse(status_code=401, content=content)
    except Exception:
        await db.rollback()
        raise
    await release_verification_attempt(redis, "session", user_id)
    await LoginSessionService.clear_login_method(user_id, token_id, redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/session/verify/reissue",
    name="Resend verification code",
    description="Resend the email verification code",
    response_model=SessionReissueResponse,
    tags=["Verification"],
    dependencies=[
        Depends(
            create_rate_limiter(Rate(3, Duration.MINUTE * 5), bucket_key="rate-limit:v2:reissue-verification-code")
        ),
    ],
)
async def reissue_verification_code(
    db: Database,
    user_agent: UserAgentInfo,
    api_version: APIVersion,
    ip_address: IPAddress,
    redis: Annotated[Redis, Depends(get_redis)],
    user_and_token: Annotated[UserAndToken, Security(get_client_user_and_token)],
) -> SessionReissueResponse:
    """Resend the email verification code.

    Args:
        db: Database session dependency.
        user_agent: User agent information.
        api_version: API version.
        ip_address: Client IP address.
        redis: Redis connection dependency.
        user_and_token: Tuple of user and auth token.

    Returns:
        SessionReissueResponse: Result of the reissue operation.
    """
    current_user, token = user_and_token
    user_id, token_id = current_user.id, token.id
    if not await LoginSessionService.check_is_need_verification(db, user_id, token_id):
        return SessionReissueResponse(success=False, message="Current session does not require verification")
    method = await LoginSessionService.verification_method(db, redis, user_id, token_id, api_version)
    if method != "mail":
        return SessionReissueResponse(success=False, message="Current session does not support code reissue")
    success, message, _ = await EmailVerificationService.resend_verification_code(
        db,
        redis,
        user_id,
        current_user.username,
        current_user.email,
        ip_address,
        user_agent,
        current_user.country_code,
    )
    return SessionReissueResponse(success=success, message=message)


@router.post(
    "/session/verify/mail-fallback",
    name="Email verification fallback",
    description=(
        "Fall back to email verification when TOTP verification is unavailable. "
        "If email verification is disabled, no switch happens and the session keeps TOTP."
    ),
    response_model=VerifyMethod,
    tags=["Verification"],
    dependencies=[
        Depends(create_rate_limiter(Rate(3, Duration.MINUTE * 5), bucket_key="rate-limit:v2:mail-fallback")),
    ],
)
async def fallback_email(
    db: Database,
    user_agent: UserAgentInfo,
    ip_address: IPAddress,
    redis: Annotated[Redis, Depends(get_redis)],
    user_and_token: Annotated[UserAndToken, Security(get_client_user_and_token)],
) -> VerifyMethod:
    """Fall back to email verification when TOTP is unavailable.

    Args:
        db: Database session dependency.
        user_agent: User agent information.
        ip_address: Client IP address.
        redis: Redis connection dependency.
        user_and_token: Tuple of user and auth token.

    Returns:
        VerifyMethod: The new verification method (mail), or ``totp`` when email
        verification is disabled and the pending challenge must keep TOTP.

    Raises:
        RequestError: If the session is already verified or no verification is configured.
    """
    current_user, token = user_and_token
    user_id, token_id = current_user.id, token.id
    await lock_user(db, user_id)
    login_session = await LoginSessionService.require_session(db, user_id, token_id, for_update=True)
    if login_session.is_verified or not (settings.enable_email_verification or settings.enable_totp_verification):
        raise RequestError(ErrorType.SESSION_FALLBACK_UNNEEDED)
    if not settings.enable_email_verification:
        # Email is not a login factor: do not switch the pending challenge away
        # from TOTP. Report the method the session keeps using.
        return VerifyMethod(method="totp")
    user_name, email, country = current_user.username, current_user.email, current_user.country_code
    await LoginSessionService.set_login_method(user_id, token_id, "mail", redis, db=db)
    await db.commit()
    success, message, response = await EmailVerificationService.resend_verification_code(
        db,
        redis,
        user_id,
        user_name,
        email,
        ip_address,
        user_agent,
        country,
    )
    # An already queued email still makes this an effective fallback.
    if not success and response.get("error") != "rate_limited":
        raise RequestError(ErrorType.USER_NOT_VERIFIED, {"method": "mail", "error": message})
    return VerifyMethod()
