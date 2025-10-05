from typing import Annotated

from app.auth import get_token_by_access_token
from app.config import settings
from app.const import SUPPORT_TOTP_VERIFICATION_VER
from app.database import User
from app.database.auth import OAuthToken, V1APIKeys
from app.models.oauth import OAuth2ClientCredentialsBearer

from .api_version import APIVersion
from .database import Database, get_redis

from fastapi import Depends, HTTPException, Security
from fastapi.security import (
    APIKeyQuery,
    HTTPBearer,
    OAuth2AuthorizationCodeBearer,
    OAuth2PasswordBearer,
    SecurityScopes,
)
from redis.asyncio import Redis
from sqlmodel import select

security = HTTPBearer()


oauth2_password = OAuth2PasswordBearer(
    tokenUrl="oauth/token",
    refreshUrl="oauth/token",
    scopes={"*": "允许访问全部 API。"},
    description="osu!lazer 或网页客户端密码登录认证，具有全部权限",
    scheme_name="Password Grant",
    auto_error=False,
)

oauth2_code = OAuth2AuthorizationCodeBearer(
    authorizationUrl="oauth/authorize",
    tokenUrl="oauth/token",
    refreshUrl="oauth/token",
    scopes={
        "chat.read": "允许代表用户读取聊天消息。",
        "chat.write": "允许代表用户发送聊天消息。",
        "chat.write_manage": ("允许代表用户加入和离开聊天频道。"),
        "delegate": ("允许作为客户端的所有者进行操作；仅适用于客户端凭证授权。"),
        "forum.write": "允许代表用户创建和编辑论坛帖子。",
        "friends.read": "允许读取用户的好友列表。",
        "identify": "允许读取用户的公开资料 (/me)。",
        "public": "允许代表用户读取公开数据。",
    },
    description="osu! OAuth 认证 （授权码认证）",
    scheme_name="Authorization Code Grant",
    auto_error=False,
)

oauth2_client_credentials = OAuth2ClientCredentialsBearer(
    tokenUrl="oauth/token",
    refreshUrl="oauth/token",
    scopes={
        "public": "允许读取公开数据。",
    },
    description="osu! OAuth 认证 （客户端凭证流）",
    scheme_name="Client Credentials Grant",
    auto_error=False,
)

v1_api_key = APIKeyQuery(name="k", scheme_name="V1 API Key", description="v1 API 密钥")


async def v1_authorize(
    db: Database,
    api_key: Annotated[str, Depends(v1_api_key)],
):
    """V1 API Key 授权"""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    api_key_record = (await db.exec(select(V1APIKeys).where(V1APIKeys.key == api_key))).first()
    if not api_key_record:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def get_client_user_and_token(
    db: Database,
    token: Annotated[str | None, Depends(oauth2_password)],
) -> tuple[User, OAuthToken]:
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token_record = await get_token_by_access_token(db, token)
    if not token_record:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = (await db.exec(select(User).where(User.id == token_record.user_id))).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return user, token_record


UserAndToken = tuple[User, OAuthToken]


async def get_client_user_no_verified(user_and_token: UserAndToken = Depends(get_client_user_and_token)):
    return user_and_token[0]


async def get_client_user(
    db: Database,
    redis: Annotated[Redis, Depends(get_redis)],
    api_version: APIVersion,
    user_and_token: UserAndToken = Depends(get_client_user_and_token),
):
    from app.service.verification_service import LoginSessionService

    user, token = user_and_token

    if await LoginSessionService.check_is_need_verification(db, user.id, token.id):
        # 获取当前验证方式
        verify_method = None
        if api_version >= SUPPORT_TOTP_VERIFICATION_VER:
            verify_method = await LoginSessionService.get_login_method(user.id, token.id, redis)

        if verify_method is None:
            # 智能选择验证方式（有TOTP优先TOTP）
            totp_key = await user.awaitable_attrs.totp_key
            verify_method = "totp" if totp_key is not None and api_version >= SUPPORT_TOTP_VERIFICATION_VER else "mail"

            # 设置选择的验证方法到Redis中，避免重复选择
            if api_version >= 20250913:
                await LoginSessionService.set_login_method(user.id, token.id, verify_method, redis)

        # 返回符合 osu! API 标准的错误响应
        error_response = {"error": "User not verified", "method": verify_method}
        raise HTTPException(status_code=401, detail=error_response)
    return user


async def _validate_token(
    db: Database,
    token: str,
    security_scopes: SecurityScopes,
) -> UserAndToken:
    token_record = await get_token_by_access_token(db, token)
    if not token_record:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    is_client = token_record.client_id in (
        settings.osu_client_id,
        settings.osu_web_client_id,
    )

    if not is_client:
        for scope in security_scopes.scopes:
            if scope not in token_record.scope.split(","):
                raise HTTPException(status_code=403, detail=f"Insufficient scope: {scope}")

    user = (await db.exec(select(User).where(User.id == token_record.user_id))).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user, token_record


async def get_current_user_and_token(
    db: Database,
    security_scopes: SecurityScopes,
    token_pw: Annotated[str | None, Depends(oauth2_password)] = None,
    token_code: Annotated[str | None, Depends(oauth2_code)] = None,
    token_client_credentials: Annotated[str | None, Depends(oauth2_client_credentials)] = None,
) -> UserAndToken:
    """获取当前认证用户"""
    token = token_pw or token_code or token_client_credentials
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return await _validate_token(db, token, security_scopes)


async def get_current_user(
    user_and_token: UserAndToken = Depends(get_current_user_and_token),
) -> User:
    return user_and_token[0]


async def get_optional_user(
    db: Database,
    security_scopes: SecurityScopes,
    token_pw: Annotated[str | None, Depends(oauth2_password)] = None,
    token_code: Annotated[str | None, Depends(oauth2_code)] = None,
    token_client_credentials: Annotated[str | None, Depends(oauth2_client_credentials)] = None,
) -> User | None:
    token = token_pw or token_code or token_client_credentials
    if not token:
        return None

    return (await _validate_token(db, token, security_scopes))[0]


ClientUser = Annotated[User, Security(get_client_user, scopes=["*"])]
