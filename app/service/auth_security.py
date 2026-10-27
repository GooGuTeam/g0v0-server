"""Small shared authentication controls backed by atomic Redis operations."""

import hashlib
import secrets

from app.models.error import ErrorType, RequestError

from redis.asyncio import Redis

# Fixed windows count failed password attempts and MFA submissions across workers.
_COUNTER_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def private_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def check_verification_limit(redis: Redis, user_id: int) -> None:
    count = await redis.eval(_COUNTER_SCRIPT, 1, f"auth:verification:{user_id}", 300)
    if int(count) > 3:
        raise RequestError(ErrorType.TOO_MANY_FAILED_ATTEMPTS, status_code=429)


async def acquire_setup_lock(redis: Redis, user_id: int) -> str | None:
    reservation = secrets.token_hex(16)
    return reservation if await redis.set(f"totp:setup-lock:{user_id}", reservation, nx=True, ex=60) else None


async def release_setup_lock(redis: Redis, user_id: int, reservation: str) -> None:
    await redis.eval(
        "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0",
        1,
        f"totp:setup-lock:{user_id}",
        reservation,
    )


async def reserve_email_send(redis: Redis, user_id: int) -> str | None:
    reservation = secrets.token_hex(16)
    accepted = await redis.set(f"email_verification_rate_limit:{user_id}", reservation, nx=True, ex=60)
    return reservation if accepted else None


async def release_email_send(redis: Redis, user_id: int, reservation: str) -> None:
    await redis.eval(
        "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0",
        1,
        f"email_verification_rate_limit:{user_id}",
        reservation,
    )


async def reserve_password_attempt(redis: Redis, account: str, ip_address: str) -> tuple[str, str, str, str] | None:
    """Count in-flight proofs as well as failures so concurrent requests cannot overrun a limit."""
    account_key = f"auth:password:account:{private_key(account.casefold())}"
    ip_key = f"auth:password:ip:{ip_address}"
    generations = await redis.eval(
        """
        if tonumber(redis.call('GET', KEYS[1]) or '0') >= 10 then return false end
        if tonumber(redis.call('GET', KEYS[2]) or '0') >= 30 then return false end
        local generations = {}
        for i = 1, 2 do
            local count = redis.call('INCR', KEYS[i])
            if count == 1 then redis.call('EXPIRE', KEYS[i], 300) end
            local generation = redis.call('GET', KEYS[i] .. ':generation')
            if not generation or count == 1 then
                generation = ARGV[1]
                redis.call('SET', KEYS[i] .. ':generation', generation, 'EX', 300)
            end
            generations[i] = generation
        end
        return generations
        """,
        2,
        account_key,
        ip_key,
        secrets.token_hex(16),
    )
    if not generations:
        return None
    return account_key, ip_key, str(generations[0]), str(generations[1])


async def release_password_attempt(redis: Redis, reservation: tuple[str, str, str, str]) -> None:
    account_key, ip_key, account_generation, ip_generation = reservation
    await redis.eval(
        """
        for i = 1, 2 do
            if redis.call('GET', KEYS[i] .. ':generation') == ARGV[i] then
                local count = tonumber(redis.call('GET', KEYS[i]) or '0')
                if count > 0 and redis.call('DECR', KEYS[i]) == 0 then
                    redis.call('DEL', KEYS[i], KEYS[i] .. ':generation')
                end
            end
        end
        return 1
        """,
        2,
        account_key,
        ip_key,
        account_generation,
        ip_generation,
    )
