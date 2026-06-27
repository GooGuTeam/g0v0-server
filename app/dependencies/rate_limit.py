import asyncio
from collections.abc import Callable
from inspect import isawaitable
from time import time_ns
from typing import Any
from uuid import uuid4

from app.config import settings
from app.dependencies.database import redis_limit_client

from fastapi import Depends
from fastapi_limiter.callback import default_callback
from fastapi_limiter.identifier import default_identifier
from pyrate_limiter import Duration, Rate
from redis.exceptions import NoScriptError
from starlette.requests import Request
from starlette.responses import Response

REDIS_RATE_LIMIT_SCRIPT = """
local bucket = KEYS[1]
local now = tonumber(ARGV[1])
local member = ARGV[2]
local weight = tonumber(ARGV[3])
local max_interval = tonumber(ARGV[4])
local rates_count = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', bucket, 0, now - max_interval)

for i=1,rates_count do
    local offset = (i - 1) * 2
    local interval = tonumber(ARGV[6 + offset])
    local limit = tonumber(ARGV[6 + offset + 1])
    local count = redis.call('ZCOUNT', bucket, now - interval, now)
    if count + weight > limit then
        return 0
    end
end

for i=1,weight do
    redis.call('ZADD', bucket, now, member..':'..i)
end
redis.call('PEXPIRE', bucket, max_interval + 10000)
return 1
"""


class RedisRateLimiter:
    def __init__(
        self,
        *rates: Rate,
        bucket_key: str,
        identifier: Callable[[Request], Any] = default_identifier,
        callback: Callable[..., Any] = default_callback,
        weight: int = 1,
    ):
        self.rates = sorted(rates, key=lambda rate: rate.interval)
        self.bucket_key = bucket_key
        self.identifier = identifier
        self.callback = callback
        self.weight = weight
        self._script_sha: str | None = None
        self._init_lock: asyncio.Lock | None = None

    async def _get_script_sha(self) -> str:
        if self._script_sha is not None:
            return self._script_sha

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._script_sha is None:
                self._script_sha = await redis_limit_client.script_load(REDIS_RATE_LIMIT_SCRIPT)
            return self._script_sha

    async def _try_acquire(self, key: str) -> bool:
        now = time_ns() // 1_000_000
        member = f"{now}:{uuid4().hex}"
        max_interval = self.rates[-1].interval
        args: list[Any] = [
            now,
            member,
            self.weight,
            max_interval,
            len(self.rates),
            *[value for rate in self.rates for value in (rate.interval, rate.limit)],
        ]

        script_sha = await self._get_script_sha()
        try:
            result = await redis_limit_client.evalsha(script_sha, 1, key, *args)
        except NoScriptError:
            self._script_sha = None
            script_sha = await self._get_script_sha()
            result = await redis_limit_client.evalsha(script_sha, 1, key, *args)

        return bool(result)

    def _get_route_index_and_dependency_index(self, request: Request) -> tuple[int, int] | None:
        current_route = request.scope.get("route")
        route_index = 0
        dep_index = 0

        for i, route in enumerate(request.app.routes):
            if current_route is route or (
                route.path == request.scope["path"] and hasattr(route, "methods") and request.method in route.methods
            ):
                route_index = i
                if hasattr(route, "endpoint") and getattr(route.endpoint, "_skip_limiter", False):
                    return None
                for j, dependency in enumerate(route.dependencies):
                    if self is dependency.dependency:
                        dep_index = j
                        break
                break

        return route_index, dep_index

    async def __call__(self, request: Request, response: Response):
        route_indexes = self._get_route_index_and_dependency_index(request)
        if route_indexes is None:
            return None

        route_index, dep_index = route_indexes
        rate_key = await self.identifier(request)
        key = f"{self.bucket_key}:{rate_key}:{route_index}:{dep_index}"
        success = await self._try_acquire(key)
        if not success:
            result = self.callback(request, response)
            if isawaitable(result):
                return await result
            return result
        return None


def create_rate_limiter(*rates: Rate, bucket_key: str) -> RedisRateLimiter:
    return RedisRateLimiter(*rates, bucket_key=bucket_key)


if settings.enable_rate_limit:
    LIMITERS = [
        Depends(create_rate_limiter(Rate(1200, Duration.MINUTE), bucket_key="rate-limit:api:minute")),
        Depends(create_rate_limiter(Rate(200, Duration.SECOND), bucket_key="rate-limit:api:second")),
    ]
else:
    LIMITERS = []
