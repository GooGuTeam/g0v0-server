from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from datetime import datetime
import json
from typing import Annotated

from app.config import settings

from fastapi import Depends
from pydantic import BaseModel
import redis as sync_redis
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


# 数据库引擎
engine = create_async_engine(
    settings.database_url,
    json_serializer=json_serializer,
    pool_size=30,  # 增加连接池大小
    max_overflow=50,  # 增加最大溢出连接数
    pool_timeout=30.0,
    pool_recycle=3600,  # 1小时回收连接
    pool_pre_ping=True,  # 启用连接预检查
)

# Redis 连接
redis_client = redis.from_url(settings.redis_url, decode_responses=True)

# Redis 消息缓存连接 (db1) - 使用同步客户端在线程池中执行
redis_message_client = sync_redis.from_url(settings.redis_url, decode_responses=True, db=1)


# 数据库依赖
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


def with_db():
    return AsyncSession(engine)


DBFactory = Callable[[], AsyncIterator[AsyncSession]]
Database = Annotated[AsyncSession, Depends(get_db)]


async def get_db_factory() -> DBFactory:
    async def _factory() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine) as session:
            yield session

    return _factory


# Redis 依赖
def get_redis():
    return redis_client


def get_redis_message():
    """获取消息专用的 Redis 客户端 (db1)"""
    return redis_message_client


def get_redis_pubsub():
    return redis_client.pubsub()
