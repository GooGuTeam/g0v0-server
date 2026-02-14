"""Admin endpoints for user session and trusted device management.

Provides endpoints for users to manage their login sessions and trusted devices
including listing, viewing, and revoking them.
"""

from typing import Annotated

from app.database.auth import OAuthToken
from app.database.verification import LoginSession, LoginSessionResp, TrustedDevice, TrustedDeviceResp
from app.dependencies.database import Database
from app.dependencies.geoip import GeoIPService
from app.dependencies.user import UserAndToken, get_client_user_and_token
from app.models.error import ErrorType, RequestError

from .router import router

from fastapi import Security
from pydantic import BaseModel
from sqlmodel import col, select


class SessionsResp(BaseModel):
    """Response model for user login sessions.

    Attributes:
        total: Total number of sessions.
        current: ID of the current session.
        sessions: List of login session details.
    """

    total: int
    current: int = 0
    sessions: list[LoginSessionResp]


@router.get(
    "/admin/sessions",
    name="Get current user's login sessions",
    tags=["User Sessions", "g0v0 API", "Admin"],
    response_model=SessionsResp,
    description="Get the list of login sessions for the current user.",
)
async def get_sessions(
    session: Database,
    user_and_token: Annotated[UserAndToken, Security(get_client_user_and_token)],
    geoip: GeoIPService,
):
    current_user, token = user_and_token
    current = 0

    sessions = (
        await session.exec(
            select(
                LoginSession,
            )
            .where(LoginSession.user_id == current_user.id, col(LoginSession.is_verified).is_(True))
            .order_by(col(LoginSession.created_at).desc())
        )
    ).all()
    resp = []
    for s in sessions:
        resp.append(LoginSessionResp.from_db(s, geoip))
        if s.token_id == token.id:
            current = s.id

    return SessionsResp(
        total=len(sessions),
        current=current,
        sessions=resp,
    )


@router.delete(
    "/admin/sessions/{session_id}",
    name="Revoke a login session",
    tags=["User Sessions", "g0v0 API", "Admin"],
    status_code=204,
    description="Revoke a specific login session.",
)
async def delete_session(
    session: Database,
    session_id: int,
    user_and_token: Annotated[UserAndToken, Security(get_client_user_and_token)],
):
    current_user, token = user_and_token
    if session_id == token.id:
        raise RequestError(ErrorType.CANNOT_DELETE_CURRENT_SESSION)

    db_session = await session.get(LoginSession, session_id)
    if not db_session or db_session.user_id != current_user.id:
        raise RequestError(ErrorType.SESSION_NOT_FOUND)

    await session.delete(db_session)

    token = await session.get(OAuthToken, db_session.token_id or 0)
    if token:
        await session.delete(token)

    await session.commit()
    return


class TrustedDevicesResp(BaseModel):
    """Response model for trusted devices.

    Attributes:
        total: Total number of trusted devices.
        current: ID of the current device.
        devices: List of trusted device details.
    """

    total: int
    current: int = 0
    devices: list[TrustedDeviceResp]


@router.get(
    "/admin/trusted-devices",
    name="Get current user's trusted devices",
    tags=["User Sessions", "g0v0 API", "Admin"],
    response_model=TrustedDevicesResp,
    description="Get the list of trusted devices for the current user.",
)
async def get_trusted_devices(
    session: Database,
    user_and_token: Annotated[UserAndToken, Security(get_client_user_and_token)],
    geoip: GeoIPService,
):
    current_user, token = user_and_token
    devices = (
        await session.exec(
            select(TrustedDevice)
            .where(TrustedDevice.user_id == current_user.id)
            .order_by(col(TrustedDevice.last_used_at).desc())
        )
    ).all()

    current_device_id = (
        await session.exec(
            select(TrustedDevice.id)
            .join(LoginSession, col(LoginSession.device_id) == TrustedDevice.id)
            .where(
                LoginSession.token_id == token.id,
                TrustedDevice.user_id == current_user.id,
            )
            .limit(1)
        )
    ).first()

    return TrustedDevicesResp(
        total=len(devices),
        current=current_device_id or 0,
        devices=[TrustedDeviceResp.from_db(device, geoip) for device in devices],
    )


@router.delete(
    "/admin/trusted-devices/{device_id}",
    name="Remove a trusted device",
    tags=["User Sessions", "g0v0 API", "Admin"],
    status_code=204,
    description="Remove a trusted device from the user's account.",
)
async def delete_trusted_device(
    session: Database,
    device_id: int,
    user_and_token: Annotated[UserAndToken, Security(get_client_user_and_token)],
):
    current_user, token = user_and_token
    device = await session.get(TrustedDevice, device_id)
    current_device_id = (
        await session.exec(
            select(TrustedDevice.id)
            .join(LoginSession, col(LoginSession.device_id) == TrustedDevice.id)
            .where(
                LoginSession.token_id == token.id,
                TrustedDevice.user_id == current_user.id,
            )
            .limit(1)
        )
    ).first()
    if device_id == current_device_id:
        raise RequestError(ErrorType.CANNOT_DELETE_CURRENT_TRUSTED_DEVICE)

    if not device or device.user_id != current_user.id:
        raise RequestError(ErrorType.TRUSTED_DEVICE_NOT_FOUND)

    await session.delete(device)
    await session.commit()
    return
