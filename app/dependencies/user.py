from typing import Annotated

from app.auth import get_token_by_access_token
from app.config import settings
from app.database import User
from app.database.auth import OAuthToken, V1APIKeys
from app.models.error import ErrorType, RequestError
from app.models.oauth import OAuth2ClientCredentialsBearer, ScopeInfo
from app.service.verification_service import LoginSessionService, SessionState, SessionStatus

from .api_version import APIVersion
from .database import Database, get_redis

from fast_depends import Depends as FastDepends
from fastapi import Depends, Request, Security
from fastapi.security import (
    APIKeyQuery,
    HTTPBearer,
    OAuth2AuthorizationCodeBearer,
    OAuth2PasswordBearer,
    SecurityScopes,
)
from redis.asyncio import Redis
from sqlmodel import select
from starlette.requests import HTTPConnection

security = HTTPBearer()


oauth2_password = OAuth2PasswordBearer(
    tokenUrl="oauth/token",
    refreshUrl="oauth/token",
    scopes={"*": "Allow to access all API."},
    description="osu!lazer or osu!lazer or web client password login authentication, with full permissions",
    scheme_name="Password Grant",
    auto_error=False,
)

# scope: (scope_description, can_delegate)
SCOPES: dict[str, ScopeInfo] = {
    "chat.read": ScopeInfo(description="Allows read chat messages on a user's behalf.", can_delegate=False),
    "chat.write": ScopeInfo(description="Allows sending chat messages on a user's behalf.", can_delegate=True),
    "chat.write_manage": ScopeInfo(
        description="Allows joining and leaving chat channels on a user's behalf.", can_delegate=True
    ),
    "delegate": ScopeInfo(
        description="Allows acting as the owner of a client; only available for Client Credentials Grant.",
        can_delegate=True,
    ),
    "forum.write": ScopeInfo(
        description="Allows creating and editing forum posts on a user's behalf.", can_delegate=True
    ),
    "forum.write_manage": ScopeInfo(description="Allows managing forum topics on a user's behalf.", can_delegate=True),
    "friends.read": ScopeInfo(description="Allows reading of the user's friend list.", can_delegate=False),
    "group_permissions": ScopeInfo(
        description="Allows delegate tokens to inherit the Resource Owner's group permissions in some cases.",
        can_delegate=False,
    ),
    "identify": ScopeInfo(description="Allows reading of the public profile of the user (/me).", can_delegate=False),
    "multiplayer.write_manage": ScopeInfo(
        description="Allows creating and managing multiplayer rooms on a user's behalf. "
        "This is a separate SignalR-based API; see documentation.",
        can_delegate=True,
    ),
    "public": ScopeInfo(
        description="Allows reading of publicly available data on behalf of the user.", can_delegate=False
    ),
}

CODE_SCOPES = {scope: SCOPES[scope] for scope in SCOPES if scope != "delegate"}
CLIENT_CREDENTIALS_SCOPES = {scope: SCOPES[scope] for scope in SCOPES if SCOPES[scope].can_delegate}

oauth2_code = OAuth2AuthorizationCodeBearer(
    authorizationUrl="oauth/authorize",
    tokenUrl="oauth/token",
    refreshUrl="oauth/token",
    scopes={scope: CODE_SCOPES[scope].description for scope in CODE_SCOPES},
    description="osu! OAuth authentication (Authorization Code Grant)",
    scheme_name="Authorization Code Grant",
    auto_error=False,
)

oauth2_client_credentials = OAuth2ClientCredentialsBearer(
    tokenUrl="oauth/token",
    refreshUrl="oauth/token",
    scopes={scope: CLIENT_CREDENTIALS_SCOPES[scope].description for scope in CLIENT_CREDENTIALS_SCOPES},
    description="osu! OAuth authentication (Client Credentials Grant)",
    scheme_name="Client Credentials Grant",
    auto_error=False,
)

v1_api_key = APIKeyQuery(name="k", scheme_name="V1 API Key", description="v1 API key")


async def v1_authorize(
    db: Database,
    api_key: Annotated[str, Depends(v1_api_key), FastDepends(v1_api_key)],
):
    """V1 API Key 授权"""
    if not api_key:
        raise RequestError(ErrorType.MISSING_API_KEY)

    api_key_record = (await db.exec(select(V1APIKeys).where(V1APIKeys.key == api_key))).first()
    if not api_key_record:
        raise RequestError(ErrorType.INVALID_API_KEY)


def request_session_state(connection: HTTPConnection | None, user: User, token: OAuthToken) -> SessionState | None:
    """Return the session decision already made by VerifySessionMiddleware, if any."""
    state = getattr(connection.state, "auth_context", None) if connection is not None else None
    if isinstance(state, SessionState) and state.user_id == user.id and state.token_id == token.id:
        return state
    return None


async def get_client_user_and_token(
    db: Database,
    request: Request,
    security_scopes: SecurityScopes,
    token: Annotated[str | None, Depends(oauth2_password), FastDepends(oauth2_password)],
) -> tuple[User, OAuthToken]:
    if token is None:
        raise RequestError(ErrorType.NOT_AUTHENTICATED)

    return await _validate_token(db, token, security_scopes, request)


UserAndToken = tuple[User, OAuthToken]


async def get_client_user_no_verified(user_and_token: UserAndToken = Depends(get_client_user_and_token)):
    return user_and_token[0]


async def get_client_user(
    db: Database,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis), FastDepends(get_redis)],
    api_version: APIVersion,
    user_and_token: UserAndToken = Depends(get_client_user_and_token),
):
    user, token = user_and_token
    state = request_session_state(request, user, token)
    if state is None:
        state = await LoginSessionService.get_state(db, redis, user.id, token.id, api_version)
    state.require_valid()
    if state.status == SessionStatus.PENDING:
        raise RequestError(ErrorType.USER_NOT_VERIFIED, {"method": state.method or "mail"})
    return user


async def _validate_token(
    db: Database,
    token: str,
    security_scopes: SecurityScopes,
    connection: HTTPConnection | None = None,
) -> UserAndToken:
    token_record = await get_token_by_access_token(db, token)
    if not token_record:
        raise RequestError(ErrorType.INVALID_OR_EXPIRED_TOKEN)

    is_client = token_record.client_id in (
        settings.osu_client_id,
        settings.osu_web_client_id,
    )

    if not is_client:
        for scope in security_scopes.scopes:
            if scope not in token_record.scope.split(","):
                raise RequestError(ErrorType.INSUFFICIENT_SCOPE, {"scope": scope})

    user = (await db.exec(select(User).where(User.id == token_record.user_id))).first()
    if not user:
        raise RequestError(ErrorType.USER_NOT_FOUND)
    if request_session_state(connection, user, token_record) is None:
        await LoginSessionService.require_session(db, user.id, token_record.id)
    return user, token_record


async def get_current_user_and_token(
    db: Database,
    request: Request,
    security_scopes: SecurityScopes,
    token_pw: Annotated[str | None, Depends(oauth2_password), FastDepends(oauth2_password)] = None,
    token_code: Annotated[str | None, Depends(oauth2_code), FastDepends(oauth2_code)] = None,
    token_client_credentials: Annotated[
        str | None, Depends(oauth2_client_credentials), FastDepends(oauth2_client_credentials)
    ] = None,
) -> UserAndToken:
    """获取当前认证用户"""
    token = token_pw or token_code or token_client_credentials
    if not token:
        raise RequestError(ErrorType.NOT_AUTHENTICATED)

    return await _validate_token(db, token, security_scopes, request)


async def get_current_user(
    user_and_token: UserAndToken = Depends(get_current_user_and_token),
) -> User:
    return user_and_token[0]


async def get_optional_user(
    db: Database,
    request: Request,
    security_scopes: SecurityScopes,
    token_pw: Annotated[str | None, Depends(oauth2_password), FastDepends(oauth2_password)] = None,
    token_code: Annotated[str | None, Depends(oauth2_code), FastDepends(oauth2_code)] = None,
    token_client_credentials: Annotated[
        str | None, Depends(oauth2_client_credentials), FastDepends(oauth2_client_credentials)
    ] = None,
) -> User | None:
    token = token_pw or token_code or token_client_credentials
    if not token:
        return None

    token_record = await get_token_by_access_token(db, token)
    if not token_record:
        raise RequestError(ErrorType.INVALID_OR_EXPIRED_TOKEN)

    is_client = token_record.client_id in (
        settings.osu_client_id,
        settings.osu_web_client_id,
    )

    if not is_client:
        for scope in security_scopes.scopes:
            if scope not in token_record.scope.split(","):
                raise RequestError(ErrorType.INSUFFICIENT_SCOPE, {"scope": scope})

    if token_record.user_id is None:
        return None

    user = (await db.exec(select(User).where(User.id == token_record.user_id))).first()
    if not user:
        raise RequestError(ErrorType.USER_NOT_FOUND)
    if request_session_state(request, user, token_record) is None:
        await LoginSessionService.require_session(db, user.id, token_record.id)
    return user


ClientUser = Annotated[User, Security(get_client_user, scopes=["*"])]
