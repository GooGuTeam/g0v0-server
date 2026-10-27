"""TOTP (Time-based One-Time Password) management endpoints.

Provides APIs for enabling, disabling, and managing two-factor authentication.
"""

from typing import Annotated, cast

from app.auth import (
    finish_create_totp_key,
    start_create_totp_key,
    totp_redis_key,
    verify_user_totp,
)
from app.database.auth import TotpKeys
from app.dependencies.database import Database, Redis
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError
from app.models.totp import FinishStatus, StartCreateTotpKeyResp
from app.service.auth_security import check_verification_limit, release_verification_attempt
from app.service.verification_service import lock_user

from .router import router

from fastapi import Body
from pydantic import BaseModel
import pyotp
from sqlmodel import select


class TotpStatusResp(BaseModel):
    """TOTP status response.

    Attributes:
        enabled: Whether TOTP is enabled.
        created_at: When TOTP was enabled (ISO format).
    """

    enabled: bool
    created_at: str | None = None


@router.get(
    "/totp/status",
    name="Check TOTP status",
    description="Check if the current user has enabled TOTP two-factor authentication",
    tags=["Authentication", "g0v0 API"],
    response_model=TotpStatusResp,
)
async def get_totp_status(
    current_user: ClientUser,
):
    totp_key = await current_user.awaitable_attrs.totp_key

    if totp_key:
        return TotpStatusResp(enabled=True, created_at=totp_key.created_at.isoformat())
    else:
        return TotpStatusResp(enabled=False)


@router.post(
    "/totp/create",
    name="Start TOTP creation flow",
    description=(
        "Start TOTP creation flow\n\n"
        "Returns TOTP secret and URI for adding account to authenticator app.\n\n"
        "Then request PUT `/api/private/totp/create` with the TOTP code from authenticator to complete setup.\n\n"
        "Creation flow expires after 5 minutes or 3 failed attempts."
    ),
    tags=["Authentication", "g0v0 API"],
    response_model=StartCreateTotpKeyResp,
    status_code=201,
)
async def start_create_totp(
    session: Database,
    redis: Redis,
    current_user: ClientUser,
):
    await lock_user(session, current_user.id)
    if (await session.exec(select(TotpKeys).where(TotpKeys.user_id == current_user.id).with_for_update())).first():
        raise RequestError(ErrorType.TOTP_ALREADY_ENABLED)

    previous = cast(dict[str, str], await redis.hgetall(totp_redis_key(current_user)))
    if previous:
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
    name="Complete TOTP creation flow",
    description=(
        "Complete TOTP creation flow by verifying the user-provided TOTP code.\n\n"
        "- On success: Enables TOTP and returns backup codes.\n- On failure: Returns error message."
    ),
    tags=["Authentication", "g0v0 API"],
    response_model=list[str],
    status_code=201,
)
async def finish_create_totp(
    session: Database,
    code: Annotated[str, Body(..., embed=True, description="User-provided TOTP code")],
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
    name="Disable TOTP two-factor authentication",
    description="Disable TOTP two-factor authentication for the current user",
    tags=["Authentication", "g0v0 API"],
    status_code=204,
)
async def disable_totp(
    session: Database,
    code: Annotated[str, Body(..., embed=True, description="User-provided TOTP code or backup code")],
    redis: Redis,
    current_user: ClientUser,
):
    user_id = current_user.id
    await lock_user(session, user_id)
    totp = (
        await session.exec(
            select(TotpKeys)
            .where(TotpKeys.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if not totp:
        raise RequestError(ErrorType.TOTP_NOT_ENABLED)
    await check_verification_limit(redis, "totp", user_id)
    if not await verify_user_totp(session, redis, user_id, code):
        raise RequestError(ErrorType.INVALID_TOTP_OR_BACKUP_CODE)
    await session.delete(totp)
    await session.commit()
    await release_verification_attempt(redis, "totp", user_id)
