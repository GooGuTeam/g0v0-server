from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
import json
from typing import Annotated

from app.config import settings

from fast_depends import Depends as FastDepends
from fastapi import Depends
from pydantic import BaseModel
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


def json_serializer(value):
    if isinstance(value, BaseModel | SQLModel):
        return value.model_dump_json()
    elif isinstance(value, datetime):
        return value.isoformat()
    return json.dumps(value)


engine = create_async_engine(
    settings.database_url,
    json_serializer=json_serializer,
    pool_size=30,
    max_overflow=50,
    pool_timeout=30.0,
    pool_recycle=3600,
    pool_pre_ping=True,
)

# Redis connection
redis_client = redis.from_url(settings.redis_url, decode_responses=True, db=0)

# Redis message cache connection (db1)
redis_message_client = redis.from_url(settings.redis_url, decode_responses=True, db=1)

# Redis binary data connection (no automatic response decoding, used for storing audio and other binary data, db2)
redis_binary_client = redis.from_url(settings.redis_url, decode_responses=False, db=2)


# Database dependency
db_session_context: ContextVar[AsyncSession | None] = ContextVar("db_session_context", default=None)


async def get_db():
    session = db_session_context.get()
    if session is None:
        session = AsyncSession(engine)
        db_session_context.set(session)
        try:
            yield session
        finally:
            await session.close()
            db_session_context.set(None)
    else:
        yield session


async def get_no_context_db():
    async with AsyncSession(engine) as session:
        yield session


@asynccontextmanager
async def with_db():
    async with AsyncSession(engine) as session:
        try:
            yield session
        finally:
            await session.close()


DBFactory = Callable[[], AsyncIterator[AsyncSession]]
Database = Annotated[AsyncSession, Depends(get_db), FastDepends(get_db)]
NoContextDB = Annotated[AsyncSession, Depends(get_no_context_db), FastDepends(get_no_context_db)]


async def get_db_factory() -> DBFactory:
    async def _factory() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine) as session:
            yield session

    return _factory


def get_redis():
    return redis_client


Redis = Annotated[redis.Redis, Depends(get_redis), FastDepends(get_redis)]


def get_redis_binary():
    """Get the Redis client for binary data (db2)"""
    return redis_binary_client


def get_redis_message() -> redis.Redis:
    """Get the Redis client for message data (db1)"""
    return redis_message_client


def get_redis_pubsub():
    return redis_client.pubsub()
