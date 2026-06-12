"""Session verification API endpoints.

This module implements email/TOTP verification flow similar to osu! for session
authentication (API v2). Supports both email verification codes and TOTP-based
two-factor authentication.
"""

from typing import Annotated, Literal

from app.auth import check_totp_backup_code, verify_totp_key_with_replay_protection
from app.config import settings
from app.const import BACKUP_CODE_LENGTH, SUPPORT_TOTP_VERIFICATION_VER
from app.database.auth import TotpKeys
from app.dependencies.api_version import APIVersion
from app.dependencies.database import Database, Redis, get_redis
from app.dependencies.geoip import IPAddress
from app.dependencies.user import UserAndToken, get_client_user_and_token
from app.dependencies.user_agent import UserAgentInfo
from app.log import log
from app.models.error import ErrorType, RequestError
from app.service.login_log_service import LoginLogService
from app.service.verification_service import (
    EmailVerificationService,
    LoginSessionService,
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
    current_user = user_and_token[0]
    token_id = user_and_token[1].id
    user_id = current_user.id

    if not await LoginSessionService.check_is_need_verification(db, user_id, token_id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    verify_method: str | None = (
        "mail"
        if api_version < SUPPORT_TOTP_VERIFICATION_VER
        else await LoginSessionService.get_login_method(user_id, token_id, redis)
    )

    login_method = "password"

    try:
        totp_key: TotpKeys | None = await current_user.awaitable_attrs.totp_key
        if verify_method is None:
            # Smart selection of verification method (ref: osu-web State.php:36)
            # Force email verification for older API versions or users without TOTP
            verify_method = "mail" if api_version < SUPPORT_TOTP_VERIFICATION_VER or totp_key is None else "totp"
            await LoginSessionService.set_login_method(user_id, token_id, verify_method, redis)
        login_method = verify_method

        if verify_method == "totp":
            if not totp_key:
                # TOTP key was deleted between verification start and now (ref: osu-web fallback mechanism)
                if settings.enable_email_verification:
                    await LoginSessionService.set_login_method(user_id, token_id, "mail", redis)
                    await EmailVerificationService.send_verification_email(
                        db,
                        redis,
                        user_id,
                        current_user.username,
                        current_user.email,
                        ip_address,
                        user_agent,
                        current_user.country_code,
                    )
                    verify_method = "mail"
                    raise VerifyFailedError("User TOTP has been deleted, switched to email verification")
                # If email verification is not enabled, consider authentication passed
                # Should not normally reach here

            elif await verify_totp_key_with_replay_protection(user_id, totp_key.secret, verification_key, redis):
                pass
            elif len(verification_key) == BACKUP_CODE_LENGTH and check_totp_backup_code(totp_key, verification_key):
                login_method = "totp_backup_code"
            else:
                # Log detailed verification failure reason (ref: osu-web error handling)
                if len(verification_key) != 6:
                    raise VerifyFailedError("TOTP code length error, should be 6 digits", reason="incorrect_length")
                elif not verification_key.isdigit():
                    raise VerifyFailedError("TOTP code format error, should be digits only", reason="incorrect_format")
                else:
                    # Could be wrong key or replay attack
                    raise VerifyFailedError(
                        "TOTP verification failed, please check if code is correct and not expired",
                        reason="incorrect_key",
                    )
        else:
            success, message = await EmailVerificationService.verify_email_code(db, redis, user_id, verification_key)
            if not success:
                raise VerifyFailedError(f"Email verification failed: {message}")

        await LoginLogService.record_login(
            db=db,
            user_id=user_id,
            request=request,
            login_method=login_method,
            user_agent=user_agent.raw_ua,
            login_success=True,
            notes=f"{login_method} verification successful",
        )
        await LoginSessionService.mark_session_verified(db, redis, user_id, token_id, ip_address, user_agent, web_uuid)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except VerifyFailedError as e:
        await LoginLogService.record_failed_login(
            db=db,
            request=request,
            attempted_username=current_user.username,
            login_method=login_method,
            notes=str(e),
        )

        # Build detailed error response (ref: osu-web error handling)
        error_response = {
            "error": str(e),
            "method": verify_method,
        }

        # Add specific error reason if available
        if hasattr(e, "reason") and e.reason:
            error_response["reason"] = e.reason

        # Resend email verification code if needed
        if hasattr(e, "should_reissue") and e.should_reissue and verify_method == "mail":
            try:
                await EmailVerificationService.send_verification_email(
                    db,
                    redis,
                    user_id,
                    current_user.username,
                    current_user.email,
                    ip_address,
                    user_agent,
                    current_user.country_code,
                )
                error_response["reissued"] = True
            except Exception:
                log("Verification").exception(
                    f"Failed to resend verification email to user {current_user.id} (token: {token_id})"
                )

        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=error_response)


@router.post(
    "/session/verify/reissue",
    name="Resend verification code",
    description="Resend the email verification code",
    response_model=SessionReissueResponse,
    tags=["Verification"],
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
    current_user = user_and_token[0]
    token_id = user_and_token[1].id
    user_id = current_user.id

    if not await LoginSessionService.check_is_need_verification(db, user_id, token_id):
        return SessionReissueResponse(success=False, message="Current session does not require verification")

    verify_method: str | None = (
        "mail"
        if api_version < SUPPORT_TOTP_VERIFICATION_VER
        else await LoginSessionService.get_login_method(user_id, token_id, redis)
    )
    if verify_method != "mail":
        return SessionReissueResponse(success=False, message="Current session does not support code reissue")

    try:
        user_id = current_user.id
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

    except ValueError:
        return SessionReissueResponse(success=False, message="Invalid user session")
    except Exception:
        return SessionReissueResponse(success=False, message="Error occurred during reissue process")


@router.post(
    "/session/verify/mail-fallback",
    name="Email verification fallback",
    description="Fall back to email verification when TOTP verification is unavailable",
    response_model=VerifyMethod,
    tags=["Verification"],
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
        VerifyMethod: The new verification method (mail).

    Raises:
        RequestError: If fallback is not needed.
    """
    current_user = user_and_token[0]
    token_id = user_and_token[1].id
    if not await LoginSessionService.get_login_method(current_user.id, token_id, redis):
        raise RequestError(ErrorType.SESSION_FALLBACK_UNNEEDED)

    await LoginSessionService.set_login_method(current_user.id, token_id, "mail", redis)
    success, message, _ = await EmailVerificationService.resend_verification_code(
        db,
        redis,
        current_user.id,
        current_user.username,
        current_user.email,
        ip_address,
        user_agent,
        current_user.country_code,
    )
    if not success:
        log("Verification").error(
            f"Failed to send fallback email to user {current_user.id} (token: {token_id}): {message}"
        )
    return VerifyMethod()
