"""OAuth application management endpoints.

Provides endpoints for creating, managing, and authorizing OAuth applications.
"""

import secrets
from typing import Annotated

from app.database.auth import OAuthClient, OAuthToken
from app.dependencies.database import Database, Redis
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError

from .router import router

from fastapi import Body
from sqlmodel import select, text


@router.post(
    "/oauth-app/create",
    name="Create OAuth application",
    description="Create a new OAuth application and generate client ID and secret",
    tags=["osu! OAuth Authentication", "g0v0 API"],
)
async def create_oauth_app(
    session: Database,
    name: Annotated[str, Body(..., max_length=100, description="Application name")],
    redirect_uris: Annotated[list[str], Body(..., description="Allowed redirect URI list")],
    current_user: ClientUser,
    description: Annotated[str, Body(description="Application description")] = "",
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
    name="Get OAuth application info",
    description="Get OAuth application details by client ID",
    tags=["osu! OAuth Authentication", "g0v0 API"],
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
    name="Get user's OAuth applications",
    description="Get all OAuth applications created by the current user",
    tags=["osu! OAuth Authentication", "g0v0 API"],
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
    name="Delete OAuth application",
    description="Delete an OAuth application and all associated tokens",
    tags=["osu! OAuth Authentication", "g0v0 API"],
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
    name="Update OAuth application",
    description="Update name, description, and redirect URIs of an OAuth application",
    tags=["osu! OAuth Authentication", "g0v0 API"],
)
async def update_oauth_app(
    session: Database,
    client_id: int,
    name: Annotated[str, Body(..., max_length=100, description="New application name")],
    redirect_uris: Annotated[list[str], Body(..., description="New redirect URI list")],
    current_user: ClientUser,
    description: Annotated[str, Body(description="New application description")] = "",
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
    name="Refresh OAuth secret",
    description="Generate a new client secret and invalidate all existing tokens",
    tags=["osu! OAuth Authentication", "g0v0 API"],
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
    name="Generate OAuth authorization code",
    description="Generate an authorization code for a user and OAuth app",
    tags=["osu! OAuth Authentication", "g0v0 API"],
)
async def generate_oauth_code(
    session: Database,
    client_id: int,
    current_user: ClientUser,
    redirect_uri: Annotated[str, Body(..., description="Redirect URI after authorization")],
    scopes: Annotated[list[str], Body(..., description="Requested permission scopes")],
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
