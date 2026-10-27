"""Email challenges, persistent login verification and trusted devices."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import secrets
import string
from typing import Literal

from app.config import settings
from app.const import SUPPORT_TOTP_VERIFICATION_VER
from app.database import EmailVerification, LoginSession, OAuthToken, TotpKeys, TrustedDevice, User
from app.helpers import as_utc, extract_user_agent, utcnow
from app.log import service_logger
from app.models.error import ErrorType, RequestError
from app.models.model import UserAgentInfo
from app.service.auth_security import release_email_send, reserve_email_send
from app.service.email_service import email_service

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

logger = service_logger("Verification")
VerificationMethod = Literal["totp", "mail"]
PENDING_SESSION_LIFETIME = timedelta(hours=1)


class SessionStatus(StrEnum):
    MISSING = "missing"
    EXPIRED = "expired"
    PENDING = "pending"
    VERIFIED = "verified"


@dataclass(frozen=True)
class SessionState:
    """A request snapshot; never retains a session or a lazy ORM relationship."""

    user_id: int
    token_id: int
    session_id: int | None
    status: SessionStatus
    method: VerificationMethod | None = None

    def require_valid(self) -> None:
        if self.status in (SessionStatus.MISSING, SessionStatus.EXPIRED):
            raise RequestError(ErrorType.INVALID_OR_EXPIRED_TOKEN)


async def lock_user(db: AsyncSession, user_id: int) -> None:
    """Serialize account authentication writes without changing the schema."""
    result = await db.exec(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )
    if result.first() is None:
        raise RequestError(ErrorType.USER_NOT_FOUND)


class EmailVerificationService:
    @staticmethod
    def generate_verification_code() -> str:
        return "".join(secrets.choice(string.digits) for _ in range(8))

    @staticmethod
    def generate_session_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    async def create_verification_record(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[EmailVerification, str]:
        await lock_user(db, user_id)
        existing = (
            await db.exec(
                select(EmailVerification)
                .where(
                    EmailVerification.user_id == user_id,
                    EmailVerification.email == email,
                    col(EmailVerification.is_used).is_(False),
                    EmailVerification.expires_at > utcnow(),
                )
                .with_for_update()
            )
        ).first()
        if existing is None:
            existing = EmailVerification(
                user_id=user_id,
                email=email,
                verification_code=EmailVerificationService.generate_verification_code(),
                expires_at=utcnow() + timedelta(minutes=10),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.add(existing)
            await db.flush()
        code, record_id, expires_at = existing.verification_code, existing.id, existing.expires_at
        await db.commit()
        await db.refresh(existing)
        await redis.set(
            f"email_verification:{user_id}:{code}",
            str(record_id),
            ex=max(1, int((as_utc(expires_at) - utcnow()).total_seconds())),
        )
        return existing, code

    @staticmethod
    async def send_verification_email_via_queue(
        email: str,
        code: str,
        username: str,
        user_id: int,
        country_code: str | None = None,
        *,
        expires_at: datetime | None = None,
    ) -> dict[str, str]:
        expires_at = expires_at or utcnow() + timedelta(minutes=10)
        subject, html, plain = email_service.render_verification_email(
            username=username, code=code, country_code=country_code, expiry_minutes=10
        )
        email_id = await email_service.enqueue_email(
            to_email=email,
            subject=subject,
            content=plain,
            html_content=html,
            metadata={
                "type": "email_verification",
                "user_id": user_id,
                "country": country_code,
                "expires_at": as_utc(expires_at).isoformat(),
            },
        )
        return {"id": email_id}

    @staticmethod
    async def send_verification_email(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        username: str,
        email: str,
        ip_address: str | None = None,
        user_agent: UserAgentInfo | None = None,
        country_code: str | None = None,
    ) -> dict[str, str]:
        if not settings.enable_email_verification:
            return {"id": "", "error": "disabled"}
        reservation = await reserve_email_send(redis, user_id)
        if reservation is None:
            return {"id": "", "error": "rate_limited"}
        try:
            record, code = await EmailVerificationService.create_verification_record(
                db, redis, user_id, email, ip_address, user_agent.raw_ua if user_agent else None
            )
            return await EmailVerificationService.send_verification_email_via_queue(
                email, code, username, user_id, country_code, expires_at=record.expires_at
            )
        except Exception:
            await db.rollback()
            await release_email_send(redis, user_id, reservation)
            logger.exception("Failed to enqueue verification email for user {}", user_id)
            return {"id": "", "error": "enqueue_failed"}

    @staticmethod
    async def verify_email_code(
        db: AsyncSession, redis: Redis, user_id: int, code: str, user_has_totp: bool = False
    ) -> tuple[bool, str]:
        """Consume in the caller's transaction, together with the session update."""
        _ = redis, user_has_totp  # Retain the service call signature; SQL is authoritative.
        if not settings.enable_email_verification:
            return False, "Email verification is disabled"
        if len(code) != 8 or not code.isascii() or not code.isdigit():
            return False, "Verification code invalid or expired"
        await lock_user(db, user_id)
        user = await db.get(User, user_id)
        verifications = (
            await db.exec(
                select(EmailVerification)
                .where(
                    EmailVerification.user_id == user_id,
                    EmailVerification.email == user.email if user else False,
                    EmailVerification.verification_code == code,
                    col(EmailVerification.is_used).is_(False),
                    EmailVerification.expires_at > utcnow(),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        if not verifications:
            return False, "Verification code invalid or expired"
        for verification in verifications:
            verification.is_used = True
            verification.used_at = utcnow()
            db.add(verification)
        await db.flush()
        # SQL is authoritative. A stale Redis index cannot authorize a used challenge.
        return True, "Verification successful"

    @staticmethod
    async def resend_verification_code(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        username: str,
        email: str,
        ip_address: str | None = None,
        user_agent: UserAgentInfo | None = None,
        country_code: str | None = None,
    ) -> tuple[bool, str, dict[str, str]]:
        response = await EmailVerificationService.send_verification_email(
            db, redis, user_id, username, email, ip_address, user_agent, country_code
        )
        if response.get("id"):
            return True, "Verification code resent", response
        message = {
            "rate_limited": "Please wait 60 seconds before resending",
            "disabled": "Email verification is disabled",
        }.get(response.get("error", ""), "Resend failed, please try again later")
        return False, message, response


class LoginSessionService:
    @staticmethod
    def get_key_for_event(session_id: str) -> str:
        return f"g0v0:{session_id}"

    @staticmethod
    def initial_method(has_totp: bool, api_version: int, trusted: bool = False) -> VerificationMethod | None:
        if settings.enable_totp_verification and has_totp:
            # Client versions select a supported factor, never whether MFA is required.
            # Keep legacy email verification when available, including on trusted devices.
            if api_version < SUPPORT_TOTP_VERIFICATION_VER and settings.enable_email_verification:
                return "mail"
            return "totp"
        if settings.enable_email_verification and not trusted:
            return "mail"
        return None

    @staticmethod
    async def find_session(
        db: AsyncSession, user_id: int, token_id: int, *, for_update: bool = False
    ) -> LoginSession | None:
        # An old duplicate pending row must never be hidden by a verified row.
        statement = (
            select(LoginSession)
            .where(LoginSession.user_id == user_id, LoginSession.token_id == token_id)
            .order_by(col(LoginSession.is_verified), col(LoginSession.created_at))
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await db.exec(statement)).first()

    @staticmethod
    def status(session: LoginSession | None) -> SessionStatus:
        if session is None:
            return SessionStatus.MISSING
        now = utcnow()
        if as_utc(session.expires_at) <= now or (
            not session.is_verified and as_utc(session.created_at) + PENDING_SESSION_LIFETIME <= now
        ):
            return SessionStatus.EXPIRED
        return SessionStatus.VERIFIED if session.is_verified else SessionStatus.PENDING

    @staticmethod
    async def require_session(
        db: AsyncSession, user_id: int, token_id: int, *, for_update: bool = False
    ) -> LoginSession:
        session = await LoginSessionService.find_session(db, user_id, token_id, for_update=for_update)
        if session is None or LoginSessionService.status(session) in (SessionStatus.MISSING, SessionStatus.EXPIRED):
            raise RequestError(ErrorType.INVALID_OR_EXPIRED_TOKEN)
        return session

    @staticmethod
    async def find_for_verification(db: AsyncSession, token: str) -> LoginSession | None:
        from app.auth import get_token_by_access_token

        record = await get_token_by_access_token(db, token)
        if record is None or record.user_id is None:
            return None
        return await LoginSessionService.require_session(db, record.user_id, record.id)

    @staticmethod
    async def create_session(
        db: AsyncSession,
        user_id: int,
        token_id: int,
        ip_address: str,
        user_agent: str | None = None,
        is_new_device: bool = False,
        web_uuid: str | None = None,
        is_verified: bool = False,
        *,
        verification_method: VerificationMethod | None = None,
        device_id: int | None = None,
        expires_at: datetime | None = None,
        commit: bool = True,
    ) -> LoginSession:
        await lock_user(db, user_id)
        token = await db.get(OAuthToken, token_id)
        if token is None or token.user_id != user_id:
            raise RequestError(ErrorType.INVALID_OR_EXPIRED_TOKEN)
        session = LoginSession(
            user_id=user_id,
            token_id=token_id,
            ip_address=ip_address,
            user_agent=user_agent,
            is_new_device=is_new_device,
            device_hash=web_uuid,
            is_verified=is_verified,
            verified_at=utcnow() if is_verified else None,
            verification_method=verification_method,
            device_id=device_id,
            expires_at=expires_at or token.refresh_token_expires_at,
        )
        db.add(session)
        await db.flush()
        if commit:
            await db.commit()
            await db.refresh(session)
        return session

    @staticmethod
    def _session_verify_redis_key(user_id: int, token_id: int) -> str:
        return f"session_verification_method:{user_id}:{token_id}"

    @staticmethod
    async def get_login_method(user_id: int, token_id: int, redis: Redis) -> VerificationMethod | None:
        method = await redis.get(LoginSessionService._session_verify_redis_key(user_id, token_id))
        if isinstance(method, bytes):
            method = method.decode()
        return method if method in ("totp", "mail") else None

    @staticmethod
    async def set_login_method(
        user_id: int,
        token_id: int,
        method: VerificationMethod,
        redis: Redis,
        *,
        db: AsyncSession | None = None,
    ) -> None:
        if db is not None:
            session = await LoginSessionService.require_session(db, user_id, token_id, for_update=True)
            session.verification_method = method
            db.add(session)
            await db.flush()
        try:
            await redis.set(LoginSessionService._session_verify_redis_key(user_id, token_id), method, ex=3600)
        except RedisError:
            if db is None:
                raise
            logger.warning("Verification method cache unavailable for token {}", token_id)

    @staticmethod
    async def clear_login_method(user_id: int, token_id: int, redis: Redis) -> None:
        try:
            await redis.delete(LoginSessionService._session_verify_redis_key(user_id, token_id))
        except RedisError:
            logger.warning("Verification method cache unavailable for token {}", token_id)

    @staticmethod
    async def verification_method(
        db: AsyncSession, redis: Redis, user_id: int, token_id: int, api_version: int = 0
    ) -> VerificationMethod | None:
        session = await LoginSessionService.require_session(db, user_id, token_id)
        if session.is_verified or not (settings.enable_totp_verification or settings.enable_email_verification):
            return None
        method = session.verification_method
        if method is None:
            try:
                method = await LoginSessionService.get_login_method(user_id, token_id, redis)
            except RedisError:
                method = None
        if method == "mail" and settings.enable_email_verification:
            return "mail"
        if method == "totp" and settings.enable_totp_verification:
            if await db.get(TotpKeys, user_id) is not None:
                return "totp"
            return "mail" if settings.enable_email_verification else None
        has_totp = await db.get(TotpKeys, user_id) is not None
        return LoginSessionService.initial_method(has_totp, api_version)

    @staticmethod
    async def get_state(
        db: AsyncSession, redis: Redis, user_id: int, token_id: int, api_version: int = 0
    ) -> SessionState:
        session = await LoginSessionService.find_session(db, user_id, token_id)
        status = LoginSessionService.status(session)
        method = None
        if status == SessionStatus.PENDING:
            method = await LoginSessionService.verification_method(db, redis, user_id, token_id, api_version)
            if not (settings.enable_totp_verification or settings.enable_email_verification):
                status = SessionStatus.VERIFIED
        return SessionState(user_id, token_id, session.id if session else None, status, method)

    @staticmethod
    async def check_is_need_verification(db: AsyncSession, user_id: int, token_id: int) -> bool:
        session = await LoginSessionService.require_session(db, user_id, token_id)
        return not session.is_verified and (settings.enable_totp_verification or settings.enable_email_verification)

    @staticmethod
    async def check_trusted_device(
        db: AsyncSession, user_id: int, ip_address: str, user_agent: UserAgentInfo, web_uuid: str | None = None
    ) -> TrustedDevice | None:
        await lock_user(db, user_id)
        statement = select(TrustedDevice).where(TrustedDevice.user_id == user_id, TrustedDevice.expires_at > utcnow())
        if user_agent.is_client:
            statement = statement.where(TrustedDevice.client_type == "lazer", TrustedDevice.ip_address == ip_address)
        elif web_uuid:
            statement = statement.where(TrustedDevice.client_type == "web", TrustedDevice.device_hash == web_uuid)
        else:
            return None
        device = (
            await db.exec(
                statement.order_by(col(TrustedDevice.id)).with_for_update().execution_options(populate_existing=True)
            )
        ).first()
        if device:
            device.last_used_at = utcnow()
            db.add(device)
        return device

    @staticmethod
    async def get_or_create_trusted_device(
        db: AsyncSession, user_id: int, ip_address: str, user_agent: UserAgentInfo, web_uuid: str | None = None
    ) -> TrustedDevice:
        await lock_user(db, user_id)
        statement = select(TrustedDevice).where(TrustedDevice.user_id == user_id)
        if user_agent.is_client:
            statement = statement.where(TrustedDevice.client_type == "lazer", TrustedDevice.ip_address == ip_address)
        elif web_uuid:
            statement = statement.where(TrustedDevice.client_type == "web", TrustedDevice.device_hash == web_uuid)
        else:
            raise ValueError("web_uuid is required for web clients")
        device = (
            await db.exec(
                statement.order_by(col(TrustedDevice.id)).with_for_update().execution_options(populate_existing=True)
            )
        ).first()
        if device is None:
            device = TrustedDevice(
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent.raw_ua,
                client_type="lazer" if user_agent.is_client else "web",
                device_hash=None if user_agent.is_client else web_uuid,
                expires_at=utcnow() + timedelta(days=settings.device_trust_duration_days),
            )
        device.last_used_at = utcnow()
        device.expires_at = utcnow() + timedelta(days=settings.device_trust_duration_days)
        db.add(device)
        await db.flush()
        return device

    create_trusted_device = get_or_create_trusted_device

    @staticmethod
    async def remove_trusted_device(db: AsyncSession, device: TrustedDevice) -> None:
        """Detach existing sessions even on historical databases without the device FK."""
        await lock_user(db, device.user_id)
        await db.exec(
            update(LoginSession)
            .where(col(LoginSession.user_id) == device.user_id, col(LoginSession.device_id) == device.id)
            .values(device_id=None)
        )
        await db.delete(device)

    @staticmethod
    async def mark_session_verified(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        token_id: int,
        ip_address: str,
        user_agent: UserAgentInfo,
        web_uuid: str | None = None,
    ) -> bool:
        _ = redis, ip_address, user_agent, web_uuid  # Trust the identity captured at login.
        await lock_user(db, user_id)
        session = await LoginSessionService.require_session(db, user_id, token_id, for_update=True)
        if session.is_verified:
            return True
        agent = extract_user_agent(session.user_agent)
        if agent.is_client or session.device_hash:
            device = await LoginSessionService.get_or_create_trusted_device(
                db, user_id, session.ip_address, agent, session.device_hash
            )
            session.device_id = device.id
        session.is_verified = True
        session.verified_at = utcnow()
        session.verification_method = None
        db.add(session)
        await db.flush()
        return True
