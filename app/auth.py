"""Authentication and authorization utilities.

This module provides authentication-related functions including password
verification, user authentication, JWT token management, TOTP (two-factor
authentication), and OAuth token storage.

Functions:
    validate_username: Validate username format and rules.
    validate_password: Validate password requirements.
    verify_password: Verify a password against a hash.
    get_password_hash: Generate a password hash.
    authenticate_user: Authenticate a user by name and password.
    create_access_token: Create a JWT access token.
    store_token: Store OAuth tokens in the database.
    verify_token: Verify and decode a JWT token.
"""

from datetime import timedelta
import hashlib
import re
import secrets
import string

from app.config import settings
from app.const import BACKUP_CODE_LENGTH
from app.database import (
    OAuthToken,
    User,
)
from app.database.auth import TotpKeys
from app.helpers import as_utc, run_in_threadpool, utcnow
from app.log import log
from app.models.totp import FinishStatus, StartCreateTotpKeyResp

import bcrypt
from jose import JWTError, jwt
from passlib.context import CryptContext
import pyotp
from redis.asyncio import Redis
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt cache (simulated application state cache)
bcrypt_cache = {}

logger = log("Auth")


def validate_username(username: str) -> list[str]:
    """Validate a username against format rules.

    Args:
        username: The username to validate.

    Returns:
        A list of validation error messages. Empty if valid.
    """
    errors = []

    if not username:
        errors.append("Username is required")
        return errors

    if len(username) < 1:
        errors.append("Username must be at least 1 characters long")

    if len(username) > 15:
        errors.append("Username must be at most 15 characters long")

    # Block whitespace and control characters (allow all other Unicode)
    if not re.search(r"^[\w \[\]-]{2,15}$", username):
        errors.append("Username contains non-allowed characters")

    if username.lower() in settings.banned_name:
        errors.append("This username is not allowed")

    return errors


def validate_password(password: str) -> list[str]:
    """Validate a password against security requirements.

    Args:
        password: The password to validate.

    Returns:
        A list of validation error messages. Empty if valid.
    """
    errors = []

    if not password:
        errors.append("Password is required")
        return errors

    if len(password) < 8:
        errors.append("Password must be at least 8 characters long")

    return errors


def verify_password_legacy(plain_password: str, bcrypt_hash: str) -> bool:
    """Verify a password using osu!'s verification method.

    The verification process:
    1. Plain password -> MD5 hash
    2. MD5 hash -> bcrypt verification

    Args:
        plain_password: The plain text password.
        bcrypt_hash: The bcrypt hash to verify against.

    Returns:
        True if the password is valid, False otherwise.
    """
    # 1. Convert plain password to MD5
    pw_md5 = hashlib.md5(plain_password.encode()).hexdigest().encode()  # noqa: S324

    # 2. Check cache
    if bcrypt_hash in bcrypt_cache:
        return bcrypt_cache[bcrypt_hash] == pw_md5

    # 3. If not in cache, perform bcrypt verification
    try:
        # Verify MD5 hash against bcrypt hash
        is_valid = bcrypt.checkpw(pw_md5, bcrypt_hash.encode())

        # If verification succeeds, cache the result
        if is_valid:
            bcrypt_cache[bcrypt_hash] = pw_md5

        return is_valid
    except Exception:
        logger.exception("Password verification error")
        return False


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password with backward compatibility.

    First attempts osu!'s legacy verification, then falls back to
    standard bcrypt verification.

    Args:
        plain_password: The plain text password.
        hashed_password: The hashed password to verify against.

    Returns:
        True if the password is valid, False otherwise.
    """
    # First try legacy verification
    if verify_password_legacy(plain_password, hashed_password):
        return True

    # If failed, try standard bcrypt verification
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate a password hash using osu!'s method.

    Args:
        password: The plain text password.

    Returns:
        The bcrypt hash of the MD5-hashed password.
    """
    # 1. Plain password -> MD5
    pw_md5 = hashlib.md5(password.encode()).hexdigest().encode()  # noqa: S324
    # 2. MD5 -> bcrypt
    pw_bcrypt = bcrypt.hashpw(pw_md5, bcrypt.gensalt())
    return pw_bcrypt.decode()


async def find_login_user(db: AsyncSession, name: str) -> User | None:
    user = (await db.exec(select(User).where(User.username == name))).first()
    if user is None:
        user = (await db.exec(select(User).where(User.email == name))).first()
    if user is None and name.isascii() and name.isdigit() and len(name) <= 10 and int(name) <= 2**31 - 1:
        user = (await db.exec(select(User).where(User.id == int(name)))).first()
    return user


async def authenticate_user_legacy(db: AsyncSession, name: str, password: str) -> User | None:
    """Authenticate a user using logic similar to from_login.

    Args:
        db: The database session.
        name: Username, email, or user ID.
        password: The plain text password.

    Returns:
        The authenticated User, or None if authentication failed.
    """
    # 1. Convert plain password to MD5
    pw_md5 = hashlib.md5(password.encode()).hexdigest()  # noqa: S324

    user = await find_login_user(db, name)
    if user is None:
        return None
    # Serialize password proof with password changes and session revocation.
    from app.service.verification_service import lock_user

    await lock_user(db, user.id)

    # 3. Validate password
    if user.pw_bcrypt is None or user.pw_bcrypt == "":
        return None

    # 4. Check cache
    if user.pw_bcrypt in bcrypt_cache:
        if bcrypt_cache[user.pw_bcrypt] == pw_md5.encode():
            return user
        else:
            return None

    # 5. Verify bcrypt
    try:
        is_valid = await run_in_threadpool(bcrypt.checkpw, pw_md5.encode(), user.pw_bcrypt.encode())
        if is_valid:
            # Cache the verification result
            bcrypt_cache[user.pw_bcrypt] = pw_md5.encode()
            return user
    except Exception:
        logger.exception(f"Authentication error for user {name}")

    return None


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User | None:
    """Authenticate a user by username and password.

    Args:
        db: The database session.
        username: Username, email, or user ID.
        password: The plain text password.

    Returns:
        The authenticated User, or None if authentication failed.
    """
    return await authenticate_user_legacy(db, username, password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token.

    Args:
        data: The payload data for the token.
        expires_delta: Optional custom expiration time.

    Returns:
        The encoded JWT token string.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = utcnow() + expires_delta
    else:
        expire = utcnow() + timedelta(minutes=settings.access_token_expire_minutes)

    # Add standard JWT claims
    to_encode.update({"exp": expire, "jti": secrets.token_hex(16)})
    if settings.jwt_audience:
        to_encode["aud"] = settings.jwt_audience
    to_encode["iss"] = str(settings.server_url)

    # Encode JWT
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def generate_refresh_token() -> str:
    """Generate a secure refresh token.

    Returns:
        A random 64-character alphanumeric string.
    """
    length = 64
    characters = string.ascii_letters + string.digits
    return "".join(secrets.choice(characters) for _ in range(length))


async def invalidate_user_tokens(db: AsyncSession, user_id: int) -> int:
    """Invalidate all tokens for a user.

    Args:
        db: The database session.
        user_id: The user ID whose tokens to invalidate.

    Returns:
        The number of tokens deleted.
    """
    from app.service.verification_service import lock_user

    await lock_user(db, user_id)
    stmt = select(OAuthToken).where(OAuthToken.user_id == user_id).with_for_update()
    result = await db.exec(stmt)
    tokens = result.all()

    count = 0
    for token in tokens:
        await db.delete(token)
        count += 1

    await db.commit()
    return count


def verify_token(token: str) -> dict | None:
    """Verify and decode a JWT access token.

    Args:
        token: The JWT token string.

    Returns:
        The decoded payload, or None if verification fails.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload
    except JWTError:
        return None


async def store_token(
    db: AsyncSession,
    user_id: int | None,
    client_id: int,
    scopes: list[str],
    access_token: str,
    refresh_token: str,
    expires_in: int,
    refresh_token_expires_in: int,
    allow_multiple_devices: bool = True,
    *,
    commit: bool = True,
) -> OAuthToken:
    """Issue a token; callers can atomically attach its login session."""
    from app.service.verification_service import lock_user

    if user_id is not None:
        await lock_user(db, user_id)
    else:
        from app.database import OAuthClient

        await db.exec(select(OAuthClient.client_id).where(OAuthClient.client_id == client_id).with_for_update())
    existing = (
        await db.exec(
            select(OAuthToken)
            .where(OAuthToken.user_id == user_id, OAuthToken.client_id == client_id)
            .order_by(col(OAuthToken.created_at).desc(), col(OAuthToken.id).desc())
            .with_for_update()
        )
    ).all()
    active = [token for token in existing if as_utc(token.refresh_token_expires_at) > utcnow()]
    keep = max(0, settings.max_tokens_per_client - 1) if allow_multiple_devices else 0
    keep_ids = {token.id for token in active[:keep]}
    for token in existing:
        if token.id not in keep_ids:
            await db.delete(token)
    record = OAuthToken(
        user_id=user_id,
        client_id=client_id,
        access_token=access_token,
        refresh_token=refresh_token,
        scope=",".join(scopes),
        expires_at=utcnow() + timedelta(seconds=expires_in),
        refresh_token_expires_at=utcnow() + timedelta(seconds=refresh_token_expires_in),
    )
    db.add(record)
    await db.flush()
    if commit:
        await db.commit()
        await db.refresh(record)
    return record


async def get_token_by_access_token(db: AsyncSession, access_token: str) -> OAuthToken | None:
    """Get a token record by access token.

    Args:
        db: The database session.
        access_token: The access token string.

    Returns:
        The OAuthToken if found and not expired, None otherwise.
    """
    statement = select(OAuthToken).where(
        OAuthToken.access_token == access_token,
        OAuthToken.expires_at > utcnow(),
    )
    return (await db.exec(statement)).first()


async def get_token_by_refresh_token(
    db: AsyncSession, refresh_token: str, *, for_update: bool = False
) -> OAuthToken | None:
    """Get a token record by refresh token.

    Args:
        db: The database session.
        refresh_token: The refresh token string.

    Returns:
        The OAuthToken if found and not expired, None otherwise.
    """
    statement = select(OAuthToken).where(
        OAuthToken.refresh_token == refresh_token,
        OAuthToken.refresh_token_expires_at > utcnow(),
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return (await db.exec(statement)).first()


async def get_user_by_authorization_code(
    db: AsyncSession, redis: Redis, client_id: int, code: str
) -> tuple[User, list[str]] | None:
    """Get user and scopes by OAuth authorization code.

    Args:
        db: The database session.
        redis: The Redis client.
        client_id: The OAuth client ID.
        code: The authorization code.

    Returns:
        A tuple of (User, scopes) if found, None otherwise.
    """
    data = await redis.eval(
        "local d = redis.call('HGETALL', KEYS[1]); redis.call('DEL', KEYS[1]); return d",
        1,
        f"oauth:code:{client_id}:{code}",
    )
    values = dict(zip(data[::2], data[1::2], strict=True))
    user_id, scopes = values.get("user_id"), values.get("scopes")
    if not user_id or not scopes:
        return None

    statement = select(User).where(User.id == int(user_id))
    user = (await db.exec(statement)).first()
    if user:
        await db.refresh(user)
        return (user, scopes.split(","))
    return None


def totp_redis_key(user: User) -> str:
    """Generate the Redis key for TOTP setup.

    Args:
        user: The user object.

    Returns:
        The Redis key string.
    """
    return f"totp:setup:{user.email}"


def _generate_totp_account_label(user: User) -> str:
    """Generate TOTP account label.

    Creates a descriptive label for use in authenticator apps,
    based on configuration choosing username or email.

    Args:
        user: The user object.

    Returns:
        The account label string.
    """
    primary_identifier = user.username if settings.totp_use_username_in_label else user.email

    # Add server info to label if configured for disambiguation in authenticator
    if settings.totp_service_name:
        return f"{primary_identifier} ({settings.totp_service_name})"
    else:
        return primary_identifier


def _generate_totp_issuer_name() -> str:
    """Generate TOTP issuer name.

    Prioritizes custom totp_issuer, falls back to service name.

    Returns:
        The issuer name string.
    """
    if settings.totp_issuer:
        return settings.totp_issuer
    elif settings.totp_service_name:
        return settings.totp_service_name
    else:
        # Fallback to default value
        return "osu! Private Server"


async def start_create_totp_key(user: User, redis: Redis) -> StartCreateTotpKeyResp:
    """Start the TOTP key creation process.

    Generates a new secret and stores it in Redis for verification.

    Args:
        user: The user object.
        redis: The Redis client.

    Returns:
        StartCreateTotpKeyResp with secret and provisioning URI.
    """
    data = await redis.eval(
        """
        if redis.call('EXISTS', KEYS[1]) == 0 then
            redis.call('HSET', KEYS[1], 'secret', ARGV[1], 'fails', 0)
            redis.call('EXPIRE', KEYS[1], 300)
        end
        return redis.call('HGET', KEYS[1], 'secret')
        """,
        1,
        totp_redis_key(user),
        pyotp.random_base32(),
    )
    secret = str(data)

    # Generate complete account label and issuer info
    account_label = _generate_totp_account_label(user)
    issuer_name = _generate_totp_issuer_name()

    return StartCreateTotpKeyResp(
        secret=secret,
        uri=pyotp.totp.TOTP(secret).provisioning_uri(name=account_label, issuer_name=issuer_name),
    )


def verify_totp_key(secret: str, code: str) -> bool:
    """Verify a TOTP code against a secret.

    Args:
        secret: The TOTP secret.
        code: The code to verify.

    Returns:
        True if the code is valid, False otherwise.
    """
    return pyotp.TOTP(secret).verify(code, valid_window=1)


async def verify_totp_key_with_replay_protection(user_id: int, secret: str, code: str, redis: Redis) -> bool:
    """Verify TOTP code with replay attack protection.

    Prevents reuse of the same code within 120 seconds.

    Args:
        user_id: The user ID.
        secret: The TOTP secret.
        code: The code to verify.
        redis: The Redis client.

    Returns:
        True if the code is valid and not replayed, False otherwise.
    """
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return False
    return bool(await redis.set(f"totp:{user_id}:{code}", "1", nx=True, ex=120))


def _generate_backup_codes(count=10, length=BACKUP_CODE_LENGTH) -> list[str]:
    """Generate TOTP backup codes.

    Args:
        count: Number of codes to generate. Defaults to 10.
        length: Length of each code. Defaults to BACKUP_CODE_LENGTH.

    Returns:
        List of generated backup codes.
    """
    alphabet = string.ascii_uppercase + string.digits
    return ["".join(secrets.choice(alphabet) for _ in range(length)) for _ in range(count)]


async def _store_totp_key(user: User, secret: str, db: AsyncSession) -> list[str]:
    """Store TOTP secret and backup codes in database.

    Args:
        user: The user object.
        secret: The TOTP secret.
        db: The database session.

    Returns:
        The list of generated backup codes.
    """
    backup_codes = _generate_backup_codes()

    def hash_codes() -> list[str]:
        return [bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode() for code in backup_codes]

    hashes = await run_in_threadpool(hash_codes)
    db.add(TotpKeys(user_id=user.id, secret=secret, backup_keys=hashes))
    await db.flush()
    return backup_codes


async def finish_create_totp_key(
    user: User, code: str, redis: Redis, db: AsyncSession
) -> tuple[FinishStatus, list[str]]:
    from app.models.error import RequestError
    from app.service.auth_security import (
        acquire_setup_lock,
        check_verification_limit,
        release_setup_lock,
        release_verification_attempt,
    )
    from app.service.verification_service import lock_user

    user_id = user.id
    setup_key = totp_redis_key(user)
    try:
        await check_verification_limit(redis, "totp-setup", user_id)
    except RequestError:
        return FinishStatus.TOO_MANY_ATTEMPTS, []
    await lock_user(db, user_id)
    if (await db.exec(select(TotpKeys).where(TotpKeys.user_id == user_id).with_for_update())).first() is not None:
        return FinishStatus.INVALID, []
    reservation = await acquire_setup_lock(redis, user_id)
    if reservation is None:
        return FinishStatus.INVALID, []
    try:
        data = await redis.hgetall(setup_key)
        if not data or "secret" not in data or "fails" not in data:
            return FinishStatus.INVALID, []
        if int(data["fails"]) >= 3:
            await redis.delete(setup_key)
            return FinishStatus.TOO_MANY_ATTEMPTS, []
        if not await verify_totp_key_with_replay_protection(user_id, str(data["secret"]), code, redis):
            fails = await redis.hincrby(setup_key, "fails", 1)
            if fails >= 3:
                await redis.delete(setup_key)
                return FinishStatus.TOO_MANY_ATTEMPTS, []
            return FinishStatus.FAILED, []
        backup_codes = await _store_totp_key(user, str(data["secret"]), db)
        await db.commit()
        await redis.delete(setup_key)
        await release_verification_attempt(redis, "totp-setup", user_id)
        return FinishStatus.SUCCESS, backup_codes
    except Exception:
        await db.rollback()
        raise
    finally:
        await release_setup_lock(redis, user_id, reservation)


async def disable_totp(user: User, db: AsyncSession) -> None:
    """Disable TOTP for a user.

    Args:
        user: The user object.
        db: The database session.
    """
    from app.service.verification_service import lock_user

    await lock_user(db, user.id)
    totp = (await db.exec(select(TotpKeys).where(TotpKeys.user_id == user.id).with_for_update())).first()
    if totp:
        await db.delete(totp)
        await db.commit()


async def check_totp_backup_code(totp: TotpKeys, code: str) -> bool:
    """Check a TOTP backup code.

    If valid, removes the code from the list of available codes.

    Args:
        totp: The TotpKeys database object.
        code: The backup code to check.

    Returns:
        True if the code is valid, False otherwise.
    """

    hashes = list(totp.backup_keys)

    def match() -> str | None:
        for hashed_code in hashes:
            if bcrypt.checkpw(code.encode(), hashed_code.encode()):
                return hashed_code
        return None

    matched = await run_in_threadpool(match)
    if matched is None:
        return False
    totp.backup_keys = [value for value in totp.backup_keys if value != matched]
    return True


async def verify_user_totp(db: AsyncSession, redis: Redis, user_id: int, code: str) -> bool:
    """Consume a TOTP or backup code under the account and credential row locks."""
    from app.service.verification_service import lock_user

    await lock_user(db, user_id)
    totp = (
        await db.exec(
            select(TotpKeys)
            .where(TotpKeys.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if totp is None:
        return False
    if len(code) == 6:
        return await verify_totp_key_with_replay_protection(user_id, totp.secret, code, redis)
    if len(code) == BACKUP_CODE_LENGTH and code.isascii():
        valid = await check_totp_backup_code(totp, code)
        if valid:
            db.add(totp)
            await db.flush()
        return valid
    return False
