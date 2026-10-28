"""Redis-backed delivery claims, acknowledgements and recoverable retry scheduling."""

from redis.asyncio import Redis

# Queue operations use Redis time so application clock differences cannot steal live claims.
_CLAIM = """
local now = tonumber(redis.call('TIME')[1])
for i = 1, 100 do
    local id = redis.call('RPOP', KEYS[1])
    if not id then return false end
    local key = 'email:' .. id
    if redis.call('HGET', key, 'status') == 'pending' then
        redis.call('HSET', key, 'status', 'sending', 'owner', ARGV[1])
        redis.call('ZADD', KEYS[2], now + tonumber(ARGV[2]), id)
        return id
    end
end
return false
"""
_RECOVER = """
local now = tonumber(redis.call('TIME')[1])
for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, 100)) do
    local key = 'email:' .. id
    redis.call('ZREM', KEYS[2], id)
    if redis.call('HGET', key, 'status') == 'sending' then
        redis.call('HSET', key, 'status', 'pending')
        redis.call('HDEL', key, 'owner')
        redis.call('LPUSH', KEYS[1], id)
    end
end
for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now, 'LIMIT', 0, 100)) do
    local key = 'email:' .. id
    redis.call('ZREM', KEYS[3], id)
    if redis.call('HGET', key, 'status') == 'retry' then
        redis.call('HSET', key, 'status', 'pending')
        redis.call('LPUSH', KEYS[1], id)
    end
end
return 1
"""
_RENEW = """
local key = 'email:' .. ARGV[1]
if redis.call('HGET', key, 'owner') ~= ARGV[2] then return 0 end
if redis.call('HGET', key, 'status') ~= 'sending' then return 0 end
redis.call('ZADD', KEYS[1], tonumber(redis.call('TIME')[1]) + tonumber(ARGV[3]), ARGV[1])
return 1
"""
_FINISH = """
local key = 'email:' .. ARGV[1]
if redis.call('HGET', key, 'owner') ~= ARGV[2] then return 0 end
if redis.call('HGET', key, 'status') ~= 'sending' then return 0 end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('HDEL', key, 'owner')
local state = ARGV[3]
local now = tonumber(redis.call('TIME')[1])
if state == 'retry' then
    local retries = redis.call('HINCRBY', key, 'retry_count', 1)
    if retries <= tonumber(ARGV[4]) then
        redis.call('ZADD', KEYS[2], now + 60 * 2 ^ (retries - 1), ARGV[1])
        redis.call('HSET', key, 'status', 'retry', 'last_retry', now)
        return 1
    end
    state = 'failed'
end
redis.call('HSET', key, 'status', state)
if state == 'sent' then redis.call('HSET', key, 'sent_at', now) end
redis.call('HDEL', key, 'content', 'html_content')
return 1
"""


class EmailQueue:
    def __init__(self, redis: Redis, lease_seconds: int = 60):
        self.redis = redis
        self.lease_seconds = lease_seconds

    async def recover(self) -> None:
        await self.redis.eval(_RECOVER, 3, "email_queue", "email_processing", "email_retries")

    async def claim(self, owner: str) -> str | None:
        value = await self.redis.eval(_CLAIM, 2, "email_queue", "email_processing", owner, self.lease_seconds)
        if isinstance(value, bytes):
            return value.decode()
        return str(value) if value else None

    async def renew(self, email_id: str, owner: str) -> bool:
        return bool(await self.redis.eval(_RENEW, 1, "email_processing", email_id, owner, self.lease_seconds))

    async def finish(self, email_id: str, owner: str, status: str, retry_limit: int = 3) -> bool:
        return bool(
            await self.redis.eval(
                _FINISH,
                2,
                "email_processing",
                "email_retries",
                email_id,
                owner,
                status,
                retry_limit,
            )
        )
