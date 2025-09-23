from __future__ import annotations

import hashlib
import re

from app.config import settings
from app.database import User
from app.log import logger

import bcrypt
from passlib.context import CryptContext
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

__all__ = [
    "authenticate_user",
    "authenticate_user_legacy",
    "get_password_hash",
    "validate_username",
    "verify_password",
    "verify_password_legacy",
]

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

bcrypt_cache: dict[str, bytes] = {}


def validate_username(username: str) -> list[str]:
    errors: list[str] = []

    if not username:
        errors.append("Username is required")
        return errors

    if len(username) < 3:
        errors.append("Username must be at least 3 characters long")

    if len(username) > 15:
        errors.append("Username must be at most 15 characters long")

    if not re.match(r"^[a-zA-Z0-9_-]+$", username):
        errors.append("Username can only contain letters, numbers, underscores, and hyphens")

    if username[0].isdigit():
        errors.append("Username cannot start with a number")

    if username.lower() in settings.banned_name:
        errors.append("This username is not allowed")

    return errors


def verify_password_legacy(plain_password: str, bcrypt_hash: str) -> bool:
    pw_md5 = hashlib.md5(plain_password.encode()).hexdigest().encode()

    cached = bcrypt_cache.get(bcrypt_hash)
    if cached is not None:
        return cached == pw_md5

    try:
        is_valid = bcrypt.checkpw(pw_md5, bcrypt_hash.encode())
    except Exception:
        logger.exception("Password verification error")
        return False

    if is_valid:
        bcrypt_cache[bcrypt_hash] = pw_md5
    return is_valid


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if verify_password_legacy(plain_password, hashed_password):
        return True

    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    pw_md5 = hashlib.md5(password.encode()).hexdigest().encode()
    pw_bcrypt = bcrypt.hashpw(pw_md5, bcrypt.gensalt())
    return pw_bcrypt.decode()


async def authenticate_user_legacy(db: AsyncSession, name: str, password: str) -> User | None:
    pw_md5 = hashlib.md5(password.encode()).hexdigest()

    user = (await db.exec(select(User).where(User.username == name))).first()
    if user is None:
        user = (await db.exec(select(User).where(User.email == name))).first()
    if user is None and name.isdigit():
        user = (await db.exec(select(User).where(User.id == int(name)))).first()
    if user is None:
        return None

    if user.pw_bcrypt is None or user.pw_bcrypt == "":
        return None

    cached = bcrypt_cache.get(user.pw_bcrypt)
    if cached is not None:
        if cached == pw_md5.encode():
            return user
        return None

    try:
        is_valid = bcrypt.checkpw(pw_md5.encode(), user.pw_bcrypt.encode())
    except Exception:
        logger.exception(f"Authentication error for user {name}")
        return None

    if not is_valid:
        return None

    bcrypt_cache[user.pw_bcrypt] = pw_md5.encode()
    return user


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    return await authenticate_user_legacy(db, username, password)
