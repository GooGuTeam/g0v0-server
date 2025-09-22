from __future__ import annotations

import secrets
import string

from app.config import settings
from app.const import BACKUP_CODE_LENGTH
from app.database import User
from app.database.auth import TotpKeys
from app.models.auth import FinishStatus, StartCreateTotpKeyResp

import bcrypt
import pyotp
from redis.asyncio import Redis
from sqlmodel.ext.asyncio.session import AsyncSession

__all__ = [
    "check_totp_backup_code",
    "disable_totp",
    "finish_create_totp_key",
    "start_create_totp_key",
    "totp_redis_key",
    "verify_totp_key",
]


def totp_redis_key(user: User) -> str:
    return f"totp:setup:{user.email}"


async def start_create_totp_key(user: User, redis: Redis) -> StartCreateTotpKeyResp:
    secret = pyotp.random_base32()
    await redis.hset(totp_redis_key(user), mapping={"secret": secret, "fails": 0})  # pyright: ignore[reportGeneralTypeIssues]
    await redis.expire(totp_redis_key(user), 300)
    return StartCreateTotpKeyResp(
        secret=secret,
        uri=pyotp.totp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=settings.totp_issuer),
    )


def verify_totp_key(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def _generate_backup_codes(count: int = 10, length: int = BACKUP_CODE_LENGTH) -> list[str]:
    alphabet = string.ascii_uppercase + string.digits
    return ["".join(secrets.choice(alphabet) for _ in range(length)) for _ in range(count)]


async def _store_totp_key(user: User, secret: str, db: AsyncSession) -> list[str]:
    backup_codes = _generate_backup_codes()
    hashed_codes = [bcrypt.hashpw(code.encode(), bcrypt.gensalt()) for code in backup_codes]
    totp_secret = TotpKeys(user_id=user.id, secret=secret, backup_keys=[code.decode() for code in hashed_codes])
    db.add(totp_secret)
    await db.commit()
    return backup_codes


async def finish_create_totp_key(
    user: User, code: str, redis: Redis, db: AsyncSession
) -> tuple[FinishStatus, list[str]]:
    data = await redis.hgetall(totp_redis_key(user))  # pyright: ignore[reportGeneralTypeIssues]
    if not data or "secret" not in data or "fails" not in data:
        return FinishStatus.INVALID, []

    secret = data["secret"]
    fails = int(data["fails"])

    if fails >= 3:
        await redis.delete(totp_redis_key(user))  # pyright: ignore[reportGeneralTypeIssues]
        return FinishStatus.TOO_MANY_ATTEMPTS, []

    if verify_totp_key(secret, code):
        await redis.delete(totp_redis_key(user))  # pyright: ignore[reportGeneralTypeIssues]
        backup_codes = await _store_totp_key(user, secret, db)
        return FinishStatus.SUCCESS, backup_codes

    fails += 1
    await redis.hset(totp_redis_key(user), "fails", str(fails))  # pyright: ignore[reportGeneralTypeIssues]
    return FinishStatus.FAILED, []


async def disable_totp(user: User, db: AsyncSession) -> None:
    totp = await db.get(TotpKeys, user.id)
    if totp:
        await db.delete(totp)
        await db.commit()


def check_totp_backup_code(totp: TotpKeys, code: str) -> bool:
    for hashed_code in totp.backup_keys:
        if bcrypt.checkpw(code.encode(), hashed_code.encode()):
            copy = totp.backup_keys[:]
            copy.remove(hashed_code)
            totp.backup_keys = copy
            return True
    return False
