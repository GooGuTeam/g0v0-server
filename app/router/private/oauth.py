import secrets
from typing import Annotated

from app.database.auth import OAuthClient, OAuthToken
from app.dependencies.database import Database, Redis
from app.dependencies.user import ClientUser

from .router import router

from app.models.error import ErrorType, RequestError
from fastapi import Body
from sqlmodel import select, text


@router.post(
    "/oauth-app/create",
    name="创建 OAuth 应用",
    description="创建一个新的 OAuth 应用程序，并生成客户端 ID 和密钥",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def create_oauth_app(
    session: Database,
    name: Annotated[str, Body(..., max_length=100, description="应用程序名称")],
    redirect_uris: Annotated[list[str], Body(..., description="允许的重定向 URI 列表")],
    current_user: ClientUser,
    description: Annotated[str, Body(description="应用程序描述")] = "",
):
    result = await session.execute(
        text(
            "SELECT AUTO_INCREMENT FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'oauth_clients'"
        )
    )
    next_id = result.one()[0]
    if next_id < 10:
        await session.execute(text("ALTER TABLE oauth_clients AUTO_INCREMENT = 10"))
        await session.commit()
        await session.refresh(current_user)

    oauth_client = OAuthClient(
        name=name,
        description=description,
        redirect_uris=redirect_uris,
        owner_id=current_user.id,
    )
    session.add(oauth_client)
    await session.commit()
    await session.refresh(oauth_client)
    return {
        "client_secret": oauth_client.client_secret,
        **oauth_client.model_dump(exclude={"client_secret"}),
    }


@router.get(
    "/oauth-apps/{client_id}",
    name="获取 OAuth 应用信息",
    description="通过客户端 ID 获取 OAuth 应用的详细信息",
    tags=["osu! OAuth 认证", "g0v0 API"],
    response_model=OAuthClient,
)
async def get_oauth_app(
    session: Database,
    client_id: int,
    current_user: ClientUser,
):
    oauth_app = await session.get(OAuthClient, client_id)
    if not oauth_app:
        raise RequestError(ErrorType.OAUTH_APP_NOT_FOUND)
    return oauth_app


@router.get(
    "/oauth-apps",
    name="获取用户的 OAuth 应用列表",
    description="获取当前用户创建的所有 OAuth 应用程序",
    tags=["osu! OAuth 认证", "g0v0 API"],
    response_model=list[OAuthClient],
)
async def get_user_oauth_apps(
    session: Database,
    current_user: ClientUser,
):
    oauth_apps = await session.exec(select(OAuthClient).where(OAuthClient.owner_id == current_user.id))
    return oauth_apps.all()


@router.delete(
    "/oauth-app/{client_id}",
    status_code=204,
    name="删除 OAuth 应用",
    description="删除指定的 OAuth 应用程序及其关联的所有令牌",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def delete_oauth_app(
    session: Database,
    client_id: int,
    current_user: ClientUser,
):
    oauth_client = await session.get(OAuthClient, client_id)
    if not oauth_client:
        raise RequestError(ErrorType.OAUTH_APP_NOT_FOUND)
    if oauth_client.owner_id != current_user.id:
        raise RequestError(ErrorType.FORBIDDEN_NOT_OWNER)

    tokens = await session.exec(select(OAuthToken).where(OAuthToken.client_id == client_id))
    for token in tokens:
        await session.delete(token)

    await session.delete(oauth_client)
    await session.commit()


@router.patch(
    "/oauth-app/{client_id}",
    name="更新 OAuth 应用",
    description="更新指定 OAuth 应用的名称、描述和重定向 URI",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def update_oauth_app(
    session: Database,
    client_id: int,
    name: Annotated[str, Body(..., max_length=100, description="应用程序新名称")],
    redirect_uris: Annotated[list[str], Body(..., description="新的重定向 URI 列表")],
    current_user: ClientUser,
    description: Annotated[str, Body(description="应用程序新描述")] = "",
):
    oauth_client = await session.get(OAuthClient, client_id)
    if not oauth_client:
        raise RequestError(ErrorType.OAUTH_APP_NOT_FOUND)
    if oauth_client.owner_id != current_user.id:
        raise RequestError(ErrorType.FORBIDDEN_NOT_OWNER)

    oauth_client.name = name
    oauth_client.description = description
    oauth_client.redirect_uris = redirect_uris

    await session.commit()
    await session.refresh(oauth_client)

    return {
        "client_secret": oauth_client.client_secret,
        **oauth_client.model_dump(exclude={"client_secret"}),
    }


@router.post(
    "/oauth-app/{client_id}/refresh",
    name="刷新 OAuth 密钥",
    description="为指定的 OAuth 应用生成新的客户端密钥，并使所有现有的令牌失效",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def refresh_secret(
    session: Database,
    client_id: int,
    current_user: ClientUser,
):
    oauth_client = await session.get(OAuthClient, client_id)
    if not oauth_client:
        raise RequestError(ErrorType.OAUTH_APP_NOT_FOUND)
    if oauth_client.owner_id != current_user.id:
        raise RequestError(ErrorType.FORBIDDEN_NOT_OWNER)

    oauth_client.client_secret = secrets.token_hex()
    tokens = await session.exec(select(OAuthToken).where(OAuthToken.client_id == client_id))
    for token in tokens:
        await session.delete(token)

    await session.commit()
    await session.refresh(oauth_client)

    return {
        "client_secret": oauth_client.client_secret,
        **oauth_client.model_dump(exclude={"client_secret"}),
    }


@router.post(
    "/oauth-app/{client_id}/code",
    name="生成 OAuth 授权码",
    description="为特定用户和 OAuth 应用生成授权码，用于授权码授权流程",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def generate_oauth_code(
    session: Database,
    client_id: int,
    current_user: ClientUser,
    redirect_uri: Annotated[str, Body(..., description="授权后重定向的 URI")],
    scopes: Annotated[list[str], Body(..., description="请求的权限范围列表")],
    redis: Redis,
):
    client = await session.get(OAuthClient, client_id)
    if not client:
        raise RequestError(ErrorType.OAUTH_APP_NOT_FOUND)

    if redirect_uri not in client.redirect_uris:
        raise RequestError(ErrorType.REDIRECT_URI_NOT_ALLOWED)

    code = secrets.token_urlsafe(80)
    await redis.hset(  # pyright: ignore[reportGeneralTypeIssues]
        f"oauth:code:{client_id}:{code}",
        mapping={"user_id": current_user.id, "scopes": ",".join(scopes)},
    )
    await redis.expire(f"oauth:code:{client_id}:{code}", 300)

    return {
        "code": code,
        "redirect_uri": redirect_uri,
        "expires_in": 300,
    }
