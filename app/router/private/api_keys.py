"""v1 API Key management endpoints.

Provides RESTful endpoints for creating, managing, and regenerating v1 API keys.
"""

import secrets
from typing import Annotated, cast

from app.database.auth import V1APIKeys
from app.dependencies.database import Database
from app.dependencies.user import ClientUser
from app.models.error import ErrorType, RequestError

from .router import router

from fastapi import Body
from pydantic import BaseModel
from sqlmodel import select


class APIKeyResponse(BaseModel):
    """Response model for API key operations."""

    id: int
    name: str
    key: str


class APIKeyListResponse(BaseModel):
    """Response model for listing API keys (without exposing the key)."""

    id: int
    name: str


@router.post(
    "/api-keys",
    name="Create v1 API key",
    description="Create a new v1 API key",
    tags=["v1 API Keys", "g0v0 API"],
    response_model=APIKeyResponse,
)
async def create_api_key(
    session: Database,
    name: Annotated[str, Body(..., max_length=100, embed=True, description="API key name")],
    current_user: ClientUser,
):
    api_key = V1APIKeys(
        name=name,
        owner_id=current_user.id,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return APIKeyResponse(id=api_key.id, name=api_key.name, key=api_key.key)


@router.get(
    "/api-keys",
    name="List v1 API keys",
    description="Get all v1 API keys owned by the current user",
    tags=["v1 API Keys", "g0v0 API"],
    response_model=list[APIKeyListResponse],
)
async def list_api_keys(
    session: Database,
    current_user: ClientUser,
):
    result = await session.exec(select(V1APIKeys.id, V1APIKeys.name).where(V1APIKeys.owner_id == current_user.id))
    return [APIKeyListResponse(id=cast(int, row[0]), name=row[1]) for row in result.all()]


@router.get(
    "/api-keys/{key_id}",
    name="Get v1 API key",
    description="Get a specific v1 API key by ID",
    tags=["v1 API Keys", "g0v0 API"],
    response_model=APIKeyResponse,
)
async def get_api_key(
    session: Database,
    key_id: int,
    current_user: ClientUser,
):
    api_key = await session.get(V1APIKeys, key_id)
    if not api_key:
        raise RequestError(ErrorType.API_KEY_NOT_FOUND)
    if api_key.owner_id != current_user.id:
        raise RequestError(ErrorType.FORBIDDEN_NOT_OWNER)
    return APIKeyResponse(id=api_key.id, name=api_key.name, key=api_key.key)


@router.patch(
    "/api-keys/{key_id}",
    name="Update v1 API key",
    description="Update the name of a v1 API key",
    tags=["v1 API Keys", "g0v0 API"],
    response_model=APIKeyListResponse,
)
async def update_api_key(
    session: Database,
    key_id: int,
    name: Annotated[str, Body(..., max_length=100, embed=True, description="New API key name")],
    current_user: ClientUser,
):
    api_key = await session.get(V1APIKeys, key_id)
    if not api_key:
        raise RequestError(ErrorType.API_KEY_NOT_FOUND)
    if api_key.owner_id != current_user.id:
        raise RequestError(ErrorType.FORBIDDEN_NOT_OWNER)

    api_key.name = name
    await session.commit()
    await session.refresh(api_key)
    return APIKeyListResponse(id=api_key.id, name=api_key.name)


@router.delete(
    "/api-keys/{key_id}",
    status_code=204,
    name="Delete v1 API key",
    description="Delete a v1 API key",
    tags=["v1 API Keys", "g0v0 API"],
)
async def delete_api_key(
    session: Database,
    key_id: int,
    current_user: ClientUser,
):
    api_key = await session.get(V1APIKeys, key_id)
    if not api_key:
        raise RequestError(ErrorType.API_KEY_NOT_FOUND)
    if api_key.owner_id != current_user.id:
        raise RequestError(ErrorType.FORBIDDEN_NOT_OWNER)

    await session.delete(api_key)
    await session.commit()


@router.post(
    "/api-keys/{key_id}/regenerate",
    name="Regenerate v1 API key",
    description="Generate a new key for an existing v1 API key",
    tags=["v1 API Keys", "g0v0 API"],
    response_model=APIKeyResponse,
)
async def regenerate_api_key(
    session: Database,
    key_id: int,
    current_user: ClientUser,
):
    api_key = await session.get(V1APIKeys, key_id)
    if not api_key:
        raise RequestError(ErrorType.API_KEY_NOT_FOUND)
    if api_key.owner_id != current_user.id:
        raise RequestError(ErrorType.FORBIDDEN_NOT_OWNER)

    api_key.key = secrets.token_hex()
    await session.commit()
    await session.refresh(api_key)
    return APIKeyResponse(id=api_key.id, name=api_key.name, key=api_key.key)
