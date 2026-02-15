"""OAuth authentication router for osu! lazer API.

This module provides OAuth 2.0 authentication endpoints including:
- User registration
- Token generation (password, refresh_token, authorization_code, client_credentials)
- Password reset functionality

Complies with osu! OAuth 2.0 specification.
"""

from datetime import timedelta
import re
from typing import Annotated, Literal

from app.auth import (
    authenticate_user,
    create_access_token,
    generate_refresh_token,
    get_password_hash,
    get_token_by_refresh_token,
    get_user_by_authorization_code,
    store_token,
    validate_password,
    validate_username,
)
from app.config import settings
from app.const import BANCHOBOT_ID, SUPPORT_TOTP_VERIFICATION_VER
from app.database import DailyChallengeStats, OAuthClient, User
from app.database.auth import TotpKeys
from app.database.statistics import UserStatistics
from app.dependencies.api_version import APIVersion
from app.dependencies.database import Database, Redis
from app.dependencies.geoip import GeoIPService, IPAddress
from app.dependencies.user_agent import UserAgentInfo
from app.helpers import utcnow
from app.log import log
from app.models.error import ErrorType, RequestError
from app.models.events.user import UserRegisteredEvent
from app.models.extended_auth import ExtendedTokenResponse
from app.models.oauth import (
    OAuthErrorResponse,
    RegistrationRequestErrors,
    TokenResponse,
    UserRegistrationErrors,
)
from app.models.score import GameMode
from app.plugins import event_hub
from app.service.login_log_service import LoginLogService
from app.service.password_reset_service import password_reset_service
from app.service.turnstile_service import turnstile_service
from app.service.verification_service import (
    EmailVerificationService,
    LoginSessionService,
)

from fastapi import APIRouter, Form, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import exists, select

logger = log("Auth")


def raise_oauth_error(error: str, error_type: ErrorType, hint: str | None = None):
    """
    Raises an OAuth error compatible with the RequestError implementation.

    Args:
        error (str): The error type in OAuth standards.
        error_type (ErrorType): The error type template.
        hint (str): Verbose detail that might be helpful.
    """

    # error_description -> Fallback message in ErrorType
    error_data = OAuthErrorResponse(error=error, error_description=error_type.value[2], hint=hint)
    raise RequestError(error_type, details=error_data.model_dump())


def validate_email(email: str) -> list[str]:
    """Validate email address format.

    Args:
        email: Email address to validate.

    Returns:
        List of validation error messages. Empty if valid.
    """
    errors = []

    if not email:
        errors.append("Email is required")
        return errors

    # Basic email format validation
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, email):
        errors.append("Please enter a valid email address")

    return errors


router = APIRouter(tags=["osu! OAuth Authentication"])


@router.post(
    "/users",
    name="Register User",
    description="User registration endpoint.",
)
async def register_user(
    db: Database,
    user_username: Annotated[str, Form(..., alias="user[username]", description="Username")],
    user_email: Annotated[str, Form(..., alias="user[user_email]", description="Email address")],
    user_password: Annotated[str, Form(..., alias="user[password]", description="Password")],
    geoip: GeoIPService,
    client_ip: IPAddress,
    user_agent: UserAgentInfo,
    cf_turnstile_response: Annotated[
        str, Form(description="Cloudflare Turnstile response token")
    ] = "XXXX.DUMMY.TOKEN.XXXX",
):
    """Register a new user account.

    Args:
        db: Database session dependency.
        user_username: Username for the new account.
        user_email: Email address for the new account.
        user_password: Password for the new account.
        geoip: GeoIP service for country detection.
        client_ip: Client IP address.
        user_agent: Parsed user agent information.
        cf_turnstile_response: Cloudflare Turnstile verification token.

    Returns:
        JSONResponse with registration result or validation errors.
    """
    # Turnstile verification (only for non-osu! clients)
    if settings.enable_turnstile_verification and not user_agent.is_client:
        success, error_msg = await turnstile_service.verify_token(cf_turnstile_response, client_ip)
        logger.info(f"Turnstile verification result: {success}, error_msg: {error_msg}")
        if not success:
            errors = RegistrationRequestErrors(message=f"Verification failed: {error_msg}")
            return JSONResponse(status_code=400, content={"form_error": errors.model_dump()})

    username_errors = validate_username(user_username)
    email_errors = validate_email(user_email)
    password_errors = validate_password(user_password)

    result = await db.exec(select(exists()).where(User.username == user_username))
    existing_user = result.first()
    if existing_user:
        username_errors.append("Username is already taken")

    result = await db.exec(select(exists()).where(User.email == user_email))
    existing_email = result.first()
    if existing_email:
        email_errors.append("Email is already taken")

    if username_errors or email_errors or password_errors:
        errors = RegistrationRequestErrors(
            user=UserRegistrationErrors(
                username=username_errors,
                user_email=email_errors,
                password=password_errors,
            )
        )

        return JSONResponse(status_code=422, content={"form_error": errors.model_dump()})

    try:
        # Get client IP and query geolocation
        country_code = None  # Default country code

        try:
            # Query IP geolocation
            geo_info = geoip.lookup(client_ip)
            if geo_info and (country_code := geo_info.get("country_iso")):
                logger.info(f"User {user_username} registering from {client_ip}, country: {country_code}")
            else:
                logger.warning(f"Could not determine country for IP {client_ip}")
        except Exception as e:
            logger.warning(f"GeoIP lookup failed for {client_ip}: {e}")
        if country_code is None:
            country_code = "CN"

        # Create new user
        # Ensure AUTO_INCREMENT starts from 3 (ID=2 is BanchoBot)
        result = await db.execute(
            text(
                "SELECT AUTO_INCREMENT FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lazer_users'"
            )
        )
        next_id = result.one()[0]
        if next_id <= 2:
            await db.execute(text("ALTER TABLE lazer_users AUTO_INCREMENT = 3"))
            await db.commit()

        new_user = User(
            username=user_username,
            email=user_email,
            pw_bcrypt=get_password_hash(user_password),
            priv=1,  # Normal user privileges
            country_code=country_code,
            join_date=utcnow(),
            last_visit=utcnow(),
            is_supporter=settings.enable_supporter_for_all_users,
            support_level=int(settings.enable_supporter_for_all_users),
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        for i in [GameMode.OSU, GameMode.TAIKO, GameMode.FRUITS, GameMode.MANIA]:
            statistics = UserStatistics(mode=i, user_id=new_user.id)
            db.add(statistics)
        if settings.enable_rx:
            for mode in (GameMode.OSURX, GameMode.TAIKORX, GameMode.FRUITSRX):
                statistics_rx = UserStatistics(mode=mode, user_id=new_user.id)
                db.add(statistics_rx)
        if settings.enable_ap:
            statistics_ap = UserStatistics(mode=GameMode.OSUAP, user_id=new_user.id)
            db.add(statistics_ap)
        daily_challenge_user_stats = DailyChallengeStats(user_id=new_user.id)
        db.add(daily_challenge_user_stats)

        event_hub.emit(
            UserRegisteredEvent(
                user_id=new_user.id,
                username=new_user.username,
                country_code=new_user.country_code,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        # Print detailed error info for debugging
        logger.exception(f"Registration error for user {user_username}")

        # Return generic error
        errors = RegistrationRequestErrors(message="An error occurred while creating your account. Please try again.")

        return JSONResponse(status_code=500, content={"form_error": errors.model_dump()})


@router.post(
    "/oauth/token",
    response_model=TokenResponse | ExtendedTokenResponse,
    name="Get Access Token",
    description=(
        "OAuth token endpoint supporting password, refresh_token, "
        "authorization_code, and client_credentials grant types."
    ),
)
async def oauth_token(
    db: Database,
    request: Request,
    user_agent: UserAgentInfo,
    ip_address: IPAddress,
    grant_type: Annotated[
        Literal["authorization_code", "refresh_token", "password", "client_credentials"],
        Form(..., description="Grant type: password, refresh_token, authorization_code, or client_credentials."),
    ],
    client_id: Annotated[int, Form(..., description="Client ID")],
    client_secret: Annotated[str, Form(..., description="Client secret")],
    redis: Redis,
    geoip: GeoIPService,
    api_version: APIVersion,
    code: Annotated[str | None, Form(description="Authorization code (required for authorization_code grant)")] = None,
    scope: Annotated[str, Form(description="Permission scope (space-separated, default '*')")] = "*",
    username: Annotated[str | None, Form(description="Username (required for password grant)")] = None,
    password: Annotated[str | None, Form(description="Password (required for password grant)")] = None,
    refresh_token: Annotated[str | None, Form(description="Refresh token (required for refresh_token grant)")] = None,
    web_uuid: Annotated[str | None, Header(include_in_schema=False, alias="X-UUID")] = None,
    cf_turnstile_response: Annotated[
        str, Form(description="Cloudflare Turnstile response token")
    ] = "XXXX.DUMMY.TOKEN.XXXX",
):
    """Generate OAuth access token.

    Supports multiple grant types:
    - password: Direct login with username/password
    - refresh_token: Refresh an existing token
    - authorization_code: Exchange authorization code for tokens
    - client_credentials: Server-to-server authentication

    Args:
        db: Database session dependency.
        request: FastAPI request object.
        user_agent: Parsed user agent information.
        ip_address: Client IP address.
        grant_type: OAuth grant type.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
        redis: Redis connection dependency.
        geoip: GeoIP service for location detection.
        api_version: API version from request headers.
        code: Authorization code for authorization_code grant.
        scope: Requested permission scopes.
        username: Username for password grant.
        password: Password for password grant.
        refresh_token: Refresh token for refresh_token grant.
        web_uuid: Web session UUID header.
        cf_turnstile_response: Cloudflare Turnstile verification token.

    Returns:
        TokenResponse or ExtendedTokenResponse with access/refresh tokens.

    Raises:
        RequestError: On authentication failure or invalid parameters.
    """
    # Turnstile verification (only for non-osu! client password grant)
    if grant_type == "password" and settings.enable_turnstile_verification and not user_agent.is_client:
        logger.debug(
            f"Turnstile check: grant_type={grant_type}, token={cf_turnstile_response[:20]}..., "
            f"enabled={settings.enable_turnstile_verification}, is_client={user_agent.is_client}"
        )
        success, error_msg = await turnstile_service.verify_token(cf_turnstile_response, ip_address)
        logger.info(f"Turnstile verification result: success={success}, error={error_msg}, ip={ip_address}")
        if not success:
            return raise_oauth_error(
                error="invalid_request",
                error_type=ErrorType.INVALID_VERIFICATION_TOKEN,
                hint=f"Verification failed: {error_msg}",
            )

    scopes = scope.split(" ")

    client = (
        await db.exec(
            select(OAuthClient).where(
                OAuthClient.client_id == client_id,
                OAuthClient.client_secret == client_secret,
            )
        )
    ).first()
    is_game_client = (client_id, client_secret) in [
        (settings.osu_client_id, settings.osu_client_secret),
        (settings.osu_web_client_id, settings.osu_web_client_secret),
    ]

    if client is None and not is_game_client:
        return raise_oauth_error(
            error="invalid_client",
            error_type=ErrorType.INVALID_AUTH_CLIENT,
            hint=(
                "Client authentication failed (e.g., unknown client, "
                "no client authentication included, "
                "or unsupported authentication method)."
            ),
        )

    if grant_type == "password":
        if not username or not password:
            return raise_oauth_error(
                error="invalid_request",
                error_type=ErrorType.SIGNIN_INFO_REQUIRED,
                hint=(
                    "The request is missing a required parameter, includes an "
                    "invalid parameter value, "
                    "includes a parameter more than once, or is otherwise malformed."
                ),
            )
        if scopes != ["*"]:
            return raise_oauth_error(
                error="invalid_scope",
                error_type=ErrorType.INVALID_SCOPE,
                hint="Only '*' scope is allowed for password grant type",
            )

        # Authenticate user
        user = await authenticate_user(db, username, password)
        if not user:
            # Record failed login attempt
            await LoginLogService.record_failed_login(
                db=db,
                request=request,
                attempted_username=username,
                login_method="password",
                notes="Invalid credentials",
            )

            return raise_oauth_error(
                error="invalid_grant",
                error_type=ErrorType.INCORRECT_SIGNIN,
                hint=(
                    "The provided authorization grant (e.g., authorization code, "
                    "resource owner credentials) "
                    "or refresh token is invalid, expired, revoked, "
                    "does not match the redirection URI used in "
                    "the authorization request, or was issued to another client."
                ),
            )

        # Ensure user object is associated with current session
        await db.refresh(user)

        user_id = user.id
        totp_key: TotpKeys | None = await user.awaitable_attrs.totp_key

        # Generate tokens
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(data={"sub": str(user_id)}, expires_delta=access_token_expires)
        refresh_token_str = generate_refresh_token()
        token = await store_token(
            db,
            user_id,
            client_id,
            scopes,
            access_token,
            refresh_token_str,
            settings.access_token_expire_minutes * 60,
            settings.refresh_token_expire_minutes * 60,
            allow_multiple_devices=settings.enable_multi_device_login,  # Use config to determine multi-device support
        )
        token_id = token.id

        # Get country code
        geo_info = geoip.lookup(ip_address)
        country_code = geo_info.get("country_iso", "XX")

        # Check if this is a new location login
        trusted_device = await LoginSessionService.check_trusted_device(db, user_id, ip_address, user_agent, web_uuid)

        # Determine verification method based on osu-web logic:
        # 1. If API version supports TOTP and user has TOTP enabled, always require TOTP (regardless of device trust)
        # 2. Otherwise, if new device and email verification is enabled, require email verification
        # 3. Otherwise, no verification needed or auto-verify
        session_verification_method = None
        if api_version >= SUPPORT_TOTP_VERIFICATION_VER and settings.enable_totp_verification and totp_key is not None:
            # TOTP verification takes priority (ref: osu-web State.php:36)
            session_verification_method = "totp"
            await LoginLogService.record_login(
                db=db,
                user_id=user_id,
                request=request,
                login_success=True,
                login_method="password_pending_verification",
                notes="TOTP verification required",
            )
        elif not trusted_device and settings.enable_email_verification:
            # New device login, email verification required
            # Refresh user object to ensure attributes are loaded
            await db.refresh(user)
            session_verification_method = "mail"
            await EmailVerificationService.send_verification_email(
                db,
                redis,
                user_id,
                user.username,
                user.email,
                ip_address,
                user_agent,
                user.country_code,
            )

            # Record login attempt requiring secondary verification
            await LoginLogService.record_login(
                db=db,
                user_id=user_id,
                request=request,
                login_success=True,
                login_method="password_pending_verification",
                notes=(
                    f"Email verification: User-Agent: {user_agent.raw_ua}, Client: {user_agent.displayed_name} "
                    f"IP: {ip_address}, Country: {country_code}"
                ),
            )
        elif not trusted_device:
            # New device login but email verification disabled, auto-verify the session
            await LoginSessionService.mark_session_verified(
                db, redis, user_id, token_id, ip_address, user_agent, web_uuid
            )
            logger.debug(f"New location login detected but email verification disabled, auto-verifying user {user_id}")
        else:
            # Not a new device, normal login
            await LoginLogService.record_login(
                db=db,
                user_id=user_id,
                request=request,
                login_success=True,
                login_method="password",
                notes=f"Normal login - IP: {ip_address}, Country: {country_code}",
            )

        if session_verification_method:
            await LoginSessionService.create_session(
                db, user_id, token_id, ip_address, user_agent.raw_ua, not trusted_device, web_uuid, False
            )
            await LoginSessionService.set_login_method(user_id, token_id, session_verification_method, redis)
        else:
            await LoginSessionService.create_session(
                db, user_id, token_id, ip_address, user_agent.raw_ua, not trusted_device, web_uuid, True
            )

        return TokenResponse(
            access_token=access_token,
            token_type="Bearer",  # noqa: S106
            expires_in=settings.access_token_expire_minutes * 60,
            refresh_token=refresh_token_str,
            scope=scope,
        )

    elif grant_type == "refresh_token":
        # Refresh token flow
        if not refresh_token:
            return raise_oauth_error(
                error="invalid_request",
                error_type=ErrorType.REFRESH_TOKEN_REQUIRED,
            )

        # Validate refresh token
        token_record = await get_token_by_refresh_token(db, refresh_token)
        if not token_record:
            return raise_oauth_error(
                error="invalid_grant",
                error_type=ErrorType.INVALID_REFRESH_TOKEN,
            )

        # Generate new access token
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(data={"sub": str(token_record.user_id)}, expires_delta=access_token_expires)
        new_refresh_token = generate_refresh_token()

        # Update token
        await store_token(
            db,
            token_record.user_id,
            client_id,
            scopes,
            access_token,
            new_refresh_token,
            settings.access_token_expire_minutes * 60,
            settings.refresh_token_expire_minutes * 60,
            allow_multiple_devices=settings.enable_multi_device_login,  # Use config to determine multi-device support
        )
        return TokenResponse(
            access_token=access_token,
            token_type="Bearer",  # noqa: S106
            expires_in=settings.access_token_expire_minutes * 60,
            refresh_token=new_refresh_token,
            scope=scope,
        )
    elif grant_type == "authorization_code":
        if client is None:
            return raise_oauth_error(
                error="invalid_client",
                error_type=ErrorType.INVALID_AUTH_CLIENT,
                hint=(
                    "Client authentication failed (e.g., unknown client, "
                    "no client authentication included, "
                    "or unsupported authentication method)."
                ),
            )

        if not code:
            return raise_oauth_error(
                error="invalid_request",
                error_type=ErrorType.AUTH_CODE_REQUIRED,
            )

        code_result = await get_user_by_authorization_code(db, redis, client_id, code)
        if not code_result:
            return raise_oauth_error(
                error="invalid_grant",
                error_type=ErrorType.INVALID_AUTH_CODE,
                hint=(
                    "The provided authorization grant (e.g., authorization code, "
                    "resource owner credentials) or refresh token is invalid, "
                    "expired, revoked, does not match the redirection URI used in "
                    "the authorization request, or was issued to another client."
                ),
            )
        user, scopes = code_result

        # Ensure user object is associated with current session
        await db.refresh(user)

        # Generate tokens
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        user_id = user.id
        access_token = create_access_token(data={"sub": str(user_id)}, expires_delta=access_token_expires)
        refresh_token_str = generate_refresh_token()

        # Store token
        await store_token(
            db,
            user_id,
            client_id,
            scopes,
            access_token,
            refresh_token_str,
            settings.access_token_expire_minutes * 60,
            settings.refresh_token_expire_minutes * 60,
            allow_multiple_devices=settings.enable_multi_device_login,  # Use config to determine multi-device support
        )

        # Log generated JWT
        logger.info(f"Generated JWT for user {user_id}: {access_token}")

        return TokenResponse(
            access_token=access_token,
            token_type="Bearer",  # noqa: S106
            expires_in=settings.access_token_expire_minutes * 60,
            refresh_token=refresh_token_str,
            scope=" ".join(scopes),
        )
    elif grant_type == "client_credentials":
        if client is None:
            return raise_oauth_error(
                error="invalid_client",
                error_type=ErrorType.INVALID_AUTH_CLIENT,
                hint=(
                    "Client authentication failed (e.g., unknown client, "
                    "no client authentication included, "
                    "or unsupported authentication method)."
                ),
            )
        elif scopes != ["public"]:
            return raise_oauth_error(
                error="invalid_scope",
                error_type=ErrorType.SCOPE_NOT_PUBLIC,
            )

        # Generate tokens
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(data={"sub": "3"}, expires_delta=access_token_expires)
        refresh_token_str = generate_refresh_token()

        # Store token
        await store_token(
            db,
            BANCHOBOT_ID,
            client_id,
            scopes,
            access_token,
            refresh_token_str,
            settings.access_token_expire_minutes * 60,
            settings.refresh_token_expire_minutes * 60,
            allow_multiple_devices=settings.enable_multi_device_login,  # Use config to determine multi-device support
        )

        return TokenResponse(
            access_token=access_token,
            token_type="Bearer",  # noqa: S106
            expires_in=settings.access_token_expire_minutes * 60,
            refresh_token=refresh_token_str,
            scope=" ".join(scopes),
        )


@router.post(
    "/password-reset/request",
    name="Request Password Reset",
    description="Request password reset verification code via email.",
)
async def request_password_reset(
    request: Request,
    email: Annotated[str, Form(..., description="Email address")],
    redis: Redis,
    ip_address: IPAddress,
    user_agent: UserAgentInfo,
    cf_turnstile_response: Annotated[
        str, Form(description="Cloudflare Turnstile response token")
    ] = "XXXX.DUMMY.TOKEN.XXXX",
):
    """Request password reset email.

    Sends a verification code to the user's email address for password reset.

    Args:
        request: FastAPI request object.
        email: Email address associated with the account.
        redis: Redis connection dependency.
        ip_address: Client IP address.
        user_agent: Parsed user agent information.
        cf_turnstile_response: Cloudflare Turnstile verification token.

    Returns:
        JSONResponse with success status and message.
    """
    # Turnstile verification (only for non-osu! clients)
    if settings.enable_turnstile_verification and not user_agent.is_client:
        success, error_msg = await turnstile_service.verify_token(cf_turnstile_response, ip_address)
        if not success:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": f"Verification failed: {error_msg}"},
            )

    # Get client info
    user_agent_str = request.headers.get("User-Agent", "")

    # Request password reset
    success, message = await password_reset_service.request_password_reset(
        email=email.lower().strip(),
        ip_address=ip_address,
        user_agent=user_agent_str,
        redis=redis,
    )

    if success:
        return JSONResponse(status_code=200, content={"success": True, "message": message})
    else:
        return JSONResponse(status_code=400, content={"success": False, "error": message})


@router.post("/password-reset/reset", name="Reset Password", description="Reset password using verification code.")
async def reset_password(
    email: Annotated[str, Form(..., description="Email address")],
    reset_code: Annotated[str, Form(..., description="Reset verification code")],
    new_password: Annotated[str, Form(..., description="New password")],
    redis: Redis,
    ip_address: IPAddress,
):
    """Reset password with verification code.

    Verifies the reset code and updates the user's password.

    Args:
        email: Email address associated with the account.
        reset_code: Verification code received via email.
        new_password: New password to set.
        redis: Redis connection dependency.
        ip_address: Client IP address.

    Returns:
        JSONResponse with success status and message.
    """
    # Reset password
    success, message = await password_reset_service.reset_password(
        email=email.lower().strip(),
        reset_code=reset_code.strip(),
        new_password=new_password,
        ip_address=ip_address,
        redis=redis,
    )

    if success:
        return JSONResponse(status_code=200, content={"success": True, "message": message})
    else:
        return JSONResponse(status_code=400, content={"success": False, "error": message})
