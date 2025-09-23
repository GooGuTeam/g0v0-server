from __future__ import annotations

from datetime import timedelta
import secrets
import string

from app.config import settings
from app.database import OAuthToken, User
from app.utils import utcnow

from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

__all__ = [
    "create_access_token",
    "generate_refresh_token",
    "get_token_by_access_token",
    "get_token_by_refresh_token",
    "get_user_by_authorization_code",
    "invalidate_user_tokens",
    "store_token",
    "verify_token",
]


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = utcnow() + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))

    to_encode.update({"exp": expire, "jti": secrets.token_hex(16)})
    if settings.jwt_audience:
        to_encode["aud"] = settings.jwt_audience
    to_encode["iss"] = str(settings.server_url)

    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def generate_refresh_token() -> str:
    length = 64
    characters = string.ascii_letters + string.digits
    return "".join(secrets.choice(characters) for _ in range(length))


async def invalidate_user_tokens(db: AsyncSession, user_id: int) -> int:
    stmt = select(OAuthToken).where(OAuthToken.user_id == user_id)
    result = await db.exec(stmt)
    tokens = result.all()

    count = 0
    for token in tokens:
        await db.delete(token)
        count += 1

    await db.commit()
    return count


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None


async def store_token(
    db: AsyncSession,
    user_id: int,
    client_id: int,
    scopes: list[str],
    access_token: str,
    refresh_token: str,
    expires_in: int,
    *,
    replace_token_id: int | None = None,
    max_tokens_per_client: int | None = None,
) -> OAuthToken:
    expires_at = utcnow() + timedelta(seconds=expires_in)

    if replace_token_id is not None:
        existing = await db.get(OAuthToken, replace_token_id)
        if existing is not None:
            await db.delete(existing)

    if max_tokens_per_client:
        statement = select(OAuthToken).where(
            OAuthToken.user_id == user_id,
            OAuthToken.client_id == client_id,
        )
        tokens = list((await db.exec(statement)).all())
        tokens.sort(key=lambda record: record.created_at or utcnow(), reverse=True)
        allowed_existing = max(max_tokens_per_client - 1, 0)
        for stale in tokens[allowed_existing:]:
            if replace_token_id is not None and stale.id == replace_token_id:
                continue
            await db.delete(stale)

    duplicate_token = (await db.exec(select(OAuthToken).where(OAuthToken.access_token == access_token))).first()
    if duplicate_token:
        await db.delete(duplicate_token)

    token_record = OAuthToken(
        user_id=user_id,
        client_id=client_id,
        access_token=access_token,
        scope=",".join(scopes),
        refresh_token=refresh_token,
        expires_at=expires_at,
    )
    db.add(token_record)
    await db.commit()
    await db.refresh(token_record)
    return token_record


async def get_token_by_access_token(db: AsyncSession, access_token: str) -> OAuthToken | None:
    statement = select(OAuthToken).where(
        OAuthToken.access_token == access_token,
        OAuthToken.expires_at > utcnow(),
    )
    return (await db.exec(statement)).first()


async def get_token_by_refresh_token(db: AsyncSession, refresh_token: str) -> OAuthToken | None:
    statement = select(OAuthToken).where(
        OAuthToken.refresh_token == refresh_token,
        OAuthToken.expires_at > utcnow(),
    )
    return (await db.exec(statement)).first()


async def get_user_by_authorization_code(
    db: AsyncSession, redis: Redis, client_id: int, code: str
) -> tuple[User, list[str]] | None:
    user_id = await redis.hget(f"oauth:code:{client_id}:{code}", "user_id")  # pyright: ignore[reportGeneralTypeIssues]
    scopes = await redis.hget(f"oauth:code:{client_id}:{code}", "scopes")  # pyright: ignore[reportGeneralTypeIssues]
    if not user_id or not scopes:
        return None

    await redis.hdel(f"oauth:code:{client_id}:{code}", "user_id", "scopes")  # pyright: ignore[reportGeneralTypeIssues]

    statement = select(User).where(User.id == int(user_id))
    user = (await db.exec(statement)).first()
    if user:
        await db.refresh(user)
        return (user, scopes.split(","))
    return None
