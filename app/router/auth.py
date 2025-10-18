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
from app.log import log
from app.models.extended_auth import ExtendedTokenResponse
from app.models.oauth import (
    OAuthErrorResponse,
    RegistrationRequestErrors,
    TokenResponse,
    UserRegistrationErrors,
)
from app.models.score import GameMode
from app.service.login_log_service import LoginLogService
from app.service.password_reset_service import password_reset_service
from app.service.turnstile_service import turnstile_service
from app.service.verification_service import (
    EmailVerificationService,
    LoginSessionService,
)
from app.utils import utcnow

from fastapi import APIRouter, Form, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import exists, select

logger = log("Auth")


def create_oauth_error_response(error: str, description: str, hint: str, status_code: int = 400):
    """创建标准的 OAuth 错误响应"""
    error_data = OAuthErrorResponse(error=error, error_description=description, hint=hint, message=description)
    return JSONResponse(status_code=status_code, content=error_data.model_dump())


def validate_email(email: str) -> list[str]:
    """验证邮箱"""
    errors = []

    if not email:
        errors.append("Email is required")
        return errors

    # 基本的邮箱格式验证
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, email):
        errors.append("Please enter a valid email address")

    return errors


router = APIRouter(tags=["osu! OAuth 认证"])


@router.post(
    "/users",
    name="注册用户",
    description="用户注册接口",
)
async def register_user(
    db: Database,
    user_username: Annotated[str, Form(..., alias="user[username]", description="用户名")],
    user_email: Annotated[str, Form(..., alias="user[user_email]", description="电子邮箱")],
    user_password: Annotated[str, Form(..., alias="user[password]", description="密码")],
    geoip: GeoIPService,
    client_ip: IPAddress,
    user_agent: UserAgentInfo,
    cf_turnstile_response: Annotated[
        str, Form(description="Cloudflare Turnstile 响应 token")
    ] = "XXXX.DUMMY.TOKEN.XXXX",
):
    # Turnstile 验证（仅对非 osu! 客户端）
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
        # 获取客户端 IP 并查询地理位置
        country_code = None  # 默认国家代码

        try:
            # 查询 IP 地理位置
            geo_info = geoip.lookup(client_ip)
            if geo_info and (country_code := geo_info.get("country_iso")):
                logger.info(f"User {user_username} registering from {client_ip}, country: {country_code}")
            else:
                logger.warning(f"Could not determine country for IP {client_ip}")
        except Exception as e:
            logger.warning(f"GeoIP lookup failed for {client_ip}: {e}")
        if country_code is None:
            country_code = "CN"

        # 创建新用户
        # 确保 AUTO_INCREMENT 值从3开始（ID=2是BanchoBot）
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
            priv=1,  # 普通用户权限
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
        await db.commit()
    except Exception:
        await db.rollback()
        # 打印详细错误信息用于调试
        logger.exception(f"Registration error for user {user_username}")

        # 返回通用错误
        errors = RegistrationRequestErrors(message="An error occurred while creating your account. Please try again.")

        return JSONResponse(status_code=500, content={"form_error": errors.model_dump()})


@router.post(
    "/oauth/token",
    response_model=TokenResponse | ExtendedTokenResponse,
    name="获取访问令牌",
    description="OAuth 令牌端点，支持密码、刷新令牌和授权码三种授权方式。",
)
async def oauth_token(
    db: Database,
    request: Request,
    user_agent: UserAgentInfo,
    ip_address: IPAddress,
    grant_type: Annotated[
        Literal["authorization_code", "refresh_token", "password", "client_credentials"],
        Form(..., description="授权类型：密码、刷新令牌和授权码三种授权方式。"),
    ],
    client_id: Annotated[int, Form(..., description="客户端 ID")],
    client_secret: Annotated[str, Form(..., description="客户端密钥")],
    redis: Redis,
    geoip: GeoIPService,
    api_version: APIVersion,
    code: Annotated[str | None, Form(description="授权码（仅授权码模式需要）")] = None,
    scope: Annotated[str, Form(description="权限范围（空格分隔，默认为 '*'）")] = "*",
    username: Annotated[str | None, Form(description="用户名（仅密码模式需要）")] = None,
    password: Annotated[str | None, Form(description="密码（仅密码模式需要）")] = None,
    refresh_token: Annotated[str | None, Form(description="刷新令牌（仅刷新令牌模式需要）")] = None,
    web_uuid: Annotated[str | None, Header(include_in_schema=False, alias="X-UUID")] = None,
    cf_turnstile_response: Annotated[
        str, Form(description="Cloudflare Turnstile 响应 token")
    ] = "XXXX.DUMMY.TOKEN.XXXX",
):
    # Turnstile 验证（仅对非 osu! 客户端的密码授权模式）
    if grant_type == "password" and settings.enable_turnstile_verification and not user_agent.is_client:
        logger.debug(
            f"Turnstile check: grant_type={grant_type}, token={cf_turnstile_response[:20]}..., "
            f"enabled={settings.enable_turnstile_verification}, is_client={user_agent.is_client}"
        )
        success, error_msg = await turnstile_service.verify_token(cf_turnstile_response, ip_address)
        logger.info(f"Turnstile verification result: success={success}, error={error_msg}, ip={ip_address}")
        if not success:
            return create_oauth_error_response(
                error="invalid_request",
                description=f"Verification failed: {error_msg}",
                hint="Invalid or expired verification token",
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
        return create_oauth_error_response(
            error="invalid_client",
            description=(
                "Client authentication failed (e.g., unknown client, "
                "no client authentication included, "
                "or unsupported authentication method)."
            ),
            hint="Invalid client credentials",
            status_code=401,
        )

    if grant_type == "password":
        if not username or not password:
            return create_oauth_error_response(
                error="invalid_request",
                description=(
                    "The request is missing a required parameter, includes an "
                    "invalid parameter value, "
                    "includes a parameter more than once, or is otherwise malformed."
                ),
                hint="Username and password required",
            )
        if scopes != ["*"]:
            return create_oauth_error_response(
                error="invalid_scope",
                description=(
                    "The requested scope is invalid, unknown, "
                    "or malformed. The client may not request "
                    "more than one scope at a time."
                ),
                hint="Only '*' scope is allowed for password grant type",
            )

        # 验证用户
        user = await authenticate_user(db, username, password)
        if not user:
            # 记录失败的登录尝试
            await LoginLogService.record_failed_login(
                db=db,
                request=request,
                attempted_username=username,
                login_method="password",
                notes="Invalid credentials",
            )

            return create_oauth_error_response(
                error="invalid_grant",
                description=(
                    "The provided authorization grant (e.g., authorization code, "
                    "resource owner credentials) "
                    "or refresh token is invalid, expired, revoked, "
                    "does not match the redirection URI used in "
                    "the authorization request, or was issued to another client."
                ),
                hint="Incorrect sign in",
            )

        # 确保用户对象与当前会话关联
        await db.refresh(user)

        user_id = user.id
        totp_key: TotpKeys | None = await user.awaitable_attrs.totp_key

        # 生成令牌
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
            allow_multiple_devices=settings.enable_multi_device_login,  # 使用配置决定是否启用多设备支持
        )
        token_id = token.id

        # 获取国家代码
        geo_info = geoip.lookup(ip_address)
        country_code = geo_info.get("country_iso", "XX")

        # 检查是否为新位置登录
        trusted_device = await LoginSessionService.check_trusted_device(db, user_id, ip_address, user_agent, web_uuid)

        # 根据 osu-web 逻辑确定验证方法：
        # 1. 如果 API 版本支持 TOTP 且用户启用了 TOTP，则始终要求 TOTP 验证（无论是否为信任设备）
        # 2. 否则，如果是新设备且启用了邮件验证，则要求邮件验证
        # 3. 否则，不需要验证或自动验证
        session_verification_method = None
        if api_version >= SUPPORT_TOTP_VERIFICATION_VER and settings.enable_totp_verification and totp_key is not None:
            # TOTP 验证优先（参考 osu-web State.php:36）
            session_verification_method = "totp"
            await LoginLogService.record_login(
                db=db,
                user_id=user_id,
                request=request,
                login_success=True,
                login_method="password_pending_verification",
                notes="需要 TOTP 验证",
            )
        elif not trusted_device and settings.enable_email_verification:
            # 如果是新设备登录，需要邮件验证
            # 刷新用户对象以确保属性已加载
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

            # 记录需要二次验证的登录尝试
            await LoginLogService.record_login(
                db=db,
                user_id=user_id,
                request=request,
                login_success=True,
                login_method="password_pending_verification",
                notes=(
                    f"邮箱验证: User-Agent: {user_agent.raw_ua}, 客户端: {user_agent.displayed_name} "
                    f"IP: {ip_address}, 国家: {country_code}"
                ),
            )
        elif not trusted_device:
            # 新设备登录但邮件验证功能被禁用，直接标记会话为已验证
            await LoginSessionService.mark_session_verified(
                db, redis, user_id, token_id, ip_address, user_agent, web_uuid
            )
            logger.debug(f"New location login detected but email verification disabled, auto-verifying user {user_id}")
        else:
            # 不是新设备登录，正常登录
            await LoginLogService.record_login(
                db=db,
                user_id=user_id,
                request=request,
                login_success=True,
                login_method="password",
                notes=f"正常登录 - IP: {ip_address}, 国家: {country_code}",
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
        # 刷新令牌流程
        if not refresh_token:
            return create_oauth_error_response(
                error="invalid_request",
                description=(
                    "The request is missing a required parameter, "
                    "includes an invalid parameter value, "
                    "includes a parameter more than once, or is otherwise malformed."
                ),
                hint="Refresh token required",
            )

        # 验证刷新令牌
        token_record = await get_token_by_refresh_token(db, refresh_token)
        if not token_record:
            return create_oauth_error_response(
                error="invalid_grant",
                description=(
                    "The provided authorization grant (e.g., authorization code, "
                    "resource owner credentials) or refresh token is "
                    "invalid, expired, revoked, "
                    "does not match the redirection URI used "
                    "in the authorization request, or was issued to another client."
                ),
                hint="Invalid refresh token",
            )

        # 生成新的访问令牌
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(data={"sub": str(token_record.user_id)}, expires_delta=access_token_expires)
        new_refresh_token = generate_refresh_token()

        # 更新令牌
        await store_token(
            db,
            token_record.user_id,
            client_id,
            scopes,
            access_token,
            new_refresh_token,
            settings.access_token_expire_minutes * 60,
            settings.refresh_token_expire_minutes * 60,
            allow_multiple_devices=settings.enable_multi_device_login,  # 使用配置决定是否启用多设备支持
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
            return create_oauth_error_response(
                error="invalid_client",
                description=(
                    "Client authentication failed (e.g., unknown client, "
                    "no client authentication included, "
                    "or unsupported authentication method)."
                ),
                hint="Invalid client credentials",
                status_code=401,
            )

        if not code:
            return create_oauth_error_response(
                error="invalid_request",
                description=(
                    "The request is missing a required parameter, "
                    "includes an invalid parameter value, "
                    "includes a parameter more than once, or is otherwise malformed."
                ),
                hint="Authorization code required",
            )

        code_result = await get_user_by_authorization_code(db, redis, client_id, code)
        if not code_result:
            return create_oauth_error_response(
                error="invalid_grant",
                description=(
                    "The provided authorization grant (e.g., authorization code, "
                    "resource owner credentials) or refresh token is invalid, "
                    "expired, revoked, does not match the redirection URI used in "
                    "the authorization request, or was issued to another client."
                ),
                hint="Invalid authorization code",
            )
        user, scopes = code_result

        # 确保用户对象与当前会话关联
        await db.refresh(user)

        # 生成令牌
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        user_id = user.id
        access_token = create_access_token(data={"sub": str(user_id)}, expires_delta=access_token_expires)
        refresh_token_str = generate_refresh_token()

        # 存储令牌
        await store_token(
            db,
            user_id,
            client_id,
            scopes,
            access_token,
            refresh_token_str,
            settings.access_token_expire_minutes * 60,
            settings.refresh_token_expire_minutes * 60,
            allow_multiple_devices=settings.enable_multi_device_login,  # 使用配置决定是否启用多设备支持
        )

        # 打印jwt
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
            return create_oauth_error_response(
                error="invalid_client",
                description=(
                    "Client authentication failed (e.g., unknown client, "
                    "no client authentication included, "
                    "or unsupported authentication method)."
                ),
                hint="Invalid client credentials",
                status_code=401,
            )
        elif scopes != ["public"]:
            return create_oauth_error_response(
                error="invalid_scope",
                description="The requested scope is invalid, unknown, or malformed.",
                hint="Scope must be 'public'",
                status_code=400,
            )

        # 生成令牌
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(data={"sub": "3"}, expires_delta=access_token_expires)
        refresh_token_str = generate_refresh_token()

        # 存储令牌
        await store_token(
            db,
            BANCHOBOT_ID,
            client_id,
            scopes,
            access_token,
            refresh_token_str,
            settings.access_token_expire_minutes * 60,
            settings.refresh_token_expire_minutes * 60,
            allow_multiple_devices=settings.enable_multi_device_login,  # 使用配置决定是否启用多设备支持
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
    name="请求密码重置",
    description="通过邮箱请求密码重置验证码",
)
async def request_password_reset(
    request: Request,
    email: Annotated[str, Form(..., description="邮箱地址")],
    redis: Redis,
    ip_address: IPAddress,
    user_agent: UserAgentInfo,
    cf_turnstile_response: Annotated[
        str, Form(description="Cloudflare Turnstile 响应 token")
    ] = "XXXX.DUMMY.TOKEN.XXXX",
):
    """
    请求密码重置
    """
    # Turnstile 验证（仅对非 osu! 客户端）
    if settings.enable_turnstile_verification and not user_agent.is_client:
        success, error_msg = await turnstile_service.verify_token(cf_turnstile_response, ip_address)
        if not success:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": f"Verification failed: {error_msg}"},
            )

    # 获取客户端信息
    user_agent_str = request.headers.get("User-Agent", "")

    # 请求密码重置
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


@router.post("/password-reset/reset", name="重置密码", description="使用验证码重置密码")
async def reset_password(
    email: Annotated[str, Form(..., description="邮箱地址")],
    reset_code: Annotated[str, Form(..., description="重置验证码")],
    new_password: Annotated[str, Form(..., description="新密码")],
    redis: Redis,
    ip_address: IPAddress,
):
    """
    重置密码
    """
    # 获取客户端信息
    # 重置密码
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
