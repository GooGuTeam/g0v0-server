from __future__ import annotations

import secrets

from app.database.auth import OAuthClient, OAuthToken
from app.database.user.lazer_user import User
from app.dependencies.database import Database, get_redis
from app.dependencies.user import get_client_user

from .router import router

from fastapi import Body, Depends, HTTPException, Security
from redis.asyncio import Redis
from sqlmodel import select, text


@router.post(
    "/oauth-app/create",
    name="创建 OAuth 应用",
    description="创建一个新的 OAuth 应用程序，并生成客户端 ID 和密钥",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def create_oauth_app(
    session: Database,
    name: str = Body(..., max_length=100, description="应用程序名称"),
    description: str = Body("", description="应用程序描述"),
    redirect_uris: list[str] = Body(..., description="允许的重定向 URI 列表"),
    current_user: User = Security(get_client_user),
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
        "client_id": oauth_client.client_id,
        "client_secret": oauth_client.client_secret,
        "redirect_uris": oauth_client.redirect_uris,
    }


@router.get(
    "/oauth-apps/{client_id}",
    name="获取 OAuth 应用信息",
    description="通过客户端 ID 获取 OAuth 应用的详细信息",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def get_oauth_app(
    session: Database,
    client_id: int,
    current_user: User = Security(get_client_user),
):
    oauth_app = await session.get(OAuthClient, client_id)
    if not oauth_app:
        raise HTTPException(status_code=404, detail="OAuth app not found")
    return {
        "name": oauth_app.name,
        "description": oauth_app.description,
        "redirect_uris": oauth_app.redirect_uris,
        "client_id": oauth_app.client_id,
    }


@router.get(
    "/oauth-apps",
    name="获取用户的 OAuth 应用列表",
    description="获取当前用户创建的所有 OAuth 应用程序",
    tags=["osu! OAuth 认证", "g0v0 API"],
)
async def get_user_oauth_apps(
    session: Database,
    current_user: User = Security(get_client_user),
):
    oauth_apps = await session.exec(select(OAuthClient).where(OAuthClient.owner_id == current_user.id))
    return [
        {
            "name": app.name,
            "description": app.description,
            "redirect_uris": app.redirect_uris,
            "client_id": app.client_id,
        }
        for app in oauth_apps
    ]


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
    current_user: User = Security(get_client_user),
):
    oauth_client = await session.get(OAuthClient, client_id)
    if not oauth_client:
        raise HTTPException(status_code=404, detail="OAuth app not found")
    if oauth_client.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden: Not the owner of this app")

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
    name: str = Body(..., max_length=100, description="应用程序新名称"),
    description: str = Body("", description="应用程序新描述"),
    redirect_uris: list[str] = Body(..., description="新的重定向 URI 列表"),
    current_user: User = Security(get_client_user),
):
    oauth_client = await session.get(OAuthClient, client_id)
    if not oauth_client:
        raise HTTPException(status_code=404, detail="OAuth app not found")
    if oauth_client.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden: Not the owner of this app")

    oauth_client.name = name
    oauth_client.description = description
    oauth_client.redirect_uris = redirect_uris

    await session.commit()
    await session.refresh(oauth_client)

    return {
        "client_id": oauth_client.client_id,
        "client_secret": oauth_client.client_secret,
        "redirect_uris": oauth_client.redirect_uris,
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
    current_user: User = Security(get_client_user),
):
    oauth_client = await session.get(OAuthClient, client_id)
    if not oauth_client:
        raise HTTPException(status_code=404, detail="OAuth app not found")
    if oauth_client.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden: Not the owner of this app")

    oauth_client.client_secret = secrets.token_hex()
    tokens = await session.exec(select(OAuthToken).where(OAuthToken.client_id == client_id))
    for token in tokens:
        await session.delete(token)

    await session.commit()
    await session.refresh(oauth_client)

    return {
        "client_id": oauth_client.client_id,
        "client_secret": oauth_client.client_secret,
        "redirect_uris": oauth_client.redirect_uris,
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
    current_user: User = Security(get_client_user),
    redirect_uri: str = Body(..., description="授权后重定向的 URI"),
    scopes: list[str] = Body(..., description="请求的权限范围列表"),
    redis: Redis = Depends(get_redis),
):
    client = await session.get(OAuthClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="OAuth app not found")

    if redirect_uri not in client.redirect_uris:
        raise HTTPException(status_code=403, detail="Redirect URI not allowed for this client")

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
