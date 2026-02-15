"""Email verification management service.

Handles email verification, login sessions, and trusted devices.
"""

from datetime import timedelta
import secrets
import string
from typing import Literal

from app.config import settings
from app.database.auth import OAuthToken
from app.database.verification import EmailVerification, LoginSession, TrustedDevice
from app.helpers import utcnow
from app.log import logger
from app.models.model import UserAgentInfo
from app.service.email_queue import email_queue

from redis.asyncio import Redis
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession


class EmailVerificationService:
    """Email verification service.

    Manages email verification codes, login sessions,
    and trusted device authentication.
    """

    @staticmethod
    def generate_verification_code() -> str:
        """Generate 8-digit verification code."""
        return "".join(secrets.choice(string.digits) for _ in range(8))

    @staticmethod
    async def send_verification_email_via_queue(
        email: str, code: str, username: str, user_id: int, country_code: str | None = None
    ) -> dict[str, str]:
        """Send verification email via email queue.

        Args:
            email: Email address to receive the verification code.
            code: Verification code.
            username: Username.
            user_id: User ID.
            country_code: Country code (used for email language selection).

        Returns:
            Dictionary in format {'id': 'message_id'}, returns email_id if using SMTP.
        """
        try:
            from app.service.email_template_service import get_email_template_service

            # Use template service to generate email content
            template_service = get_email_template_service()
            subject, html_content, plain_content = template_service.render_verification_email(
                username=username,
                code=code,
                country_code=country_code,
                expiry_minutes=10,
            )
            # Prepare metadata
            metadata = {"type": "email_verification", "user_id": user_id, "code": code, "country": country_code}

            # If using MailerSend, send directly and return message_id
            if settings.email_provider == "mailersend":
                from app.service.mailersend_service import get_mailersend_service

                mailersend_service = get_mailersend_service()
                response = await mailersend_service.send_email(
                    to_email=email,
                    subject=subject,
                    content=plain_content,
                    html_content=html_content,
                    metadata=metadata,
                )
                return response
            else:
                # Use SMTP queue to send
                email_id = await email_queue.enqueue_email(
                    to_email=email,
                    subject=subject,
                    content=plain_content,
                    html_content=html_content,
                    metadata=metadata,
                )
                return {"id": email_id}

        except Exception as e:
            logger.error(f"Failed to enqueue email: {e}")
            return {"id": ""}

    @staticmethod
    def generate_session_token() -> str:
        """Generate session token."""
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
        """Create email verification record."""

        # Check for unexpired verification code
        existing_result = await db.exec(
            select(EmailVerification).where(
                EmailVerification.user_id == user_id,
                EmailVerification.is_used == False,  # noqa: E712
                EmailVerification.expires_at > utcnow(),
            )
        )
        existing = existing_result.first()

        if existing:
            # If unexpired verification code exists, return it directly
            return existing, existing.verification_code

        # Generate new verification code
        code = EmailVerificationService.generate_verification_code()

        # Create verification record
        verification = EmailVerification(
            user_id=user_id,
            email=email,
            verification_code=code,
            expires_at=utcnow() + timedelta(minutes=10),  # 10-minute expiration
            ip_address=ip_address,
            user_agent=user_agent,
        )

        db.add(verification)
        await db.commit()
        await db.refresh(verification)

        # Store in Redis (for quick verification)
        await redis.setex(
            f"email_verification:{user_id}:{code}",
            600,  # 10-minute expiration
            str(verification.id) if verification.id else "0",
        )

        logger.info(f"Created verification code for user {user_id}: {code}")
        return verification, code

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
        """Send verification email.

        Args:
            db: Database session.
            redis: Redis client.
            user_id: User ID.
            username: Username.
            email: Email address.
            ip_address: IP address.
            user_agent: User agent information.
            country_code: Country code (used for email language selection).

        Returns:
            Dictionary in format {'id': 'message_id'}.
        """
        try:
            # Check if email verification feature is enabled
            if not settings.enable_email_verification:
                logger.debug(f"Email verification is disabled, skipping for user {user_id}")
                return {"id": "disabled"}  # Return special ID indicating feature is disabled

            # Detect client info
            logger.info(f"Detected client for user {user_id}: {user_agent}")

            # Create verification record
            (
                _,
                code,
            ) = await EmailVerificationService.create_verification_record(
                db, redis, user_id, email, ip_address, user_agent.raw_ua if user_agent else None
            )

            # Use email queue to send verification email
            response = await EmailVerificationService.send_verification_email_via_queue(
                email, code, username, user_id, country_code
            )

            if response and response.get("id"):
                logger.info(
                    f"Successfully sent verification email to {email} (user: {username}), message_id: {response['id']}"
                )
                return response
            else:
                logger.error(f"Failed to send verification email: {email} (user: {username})")
                return {"id": ""}

        except Exception as e:
            logger.error(f"Exception during sending verification email: {e}")
            return {"id": ""}

    @staticmethod
    async def verify_email_code(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        code: str,
    ) -> tuple[bool, str]:
        """Verify email verification code."""
        try:
            # Check if email verification feature is enabled
            if not settings.enable_email_verification:
                logger.debug(f"Email verification is disabled, auto-approving for user {user_id}")
                return True, "Verification successful (email verification disabled)"

            # Check Redis first
            verification_id = await redis.get(f"email_verification:{user_id}:{code}")
            if not verification_id:
                return False, "Verification code invalid or expired"

            # Get verification record from database
            result = await db.exec(
                select(EmailVerification).where(
                    EmailVerification.id == int(verification_id),
                    EmailVerification.user_id == user_id,
                    EmailVerification.verification_code == code,
                    EmailVerification.is_used == False,  # noqa: E712
                    EmailVerification.expires_at > utcnow(),
                )
            )

            verification = result.first()
            if not verification:
                return False, "Verification code invalid or expired"

            # Mark as used
            verification.is_used = True
            verification.used_at = utcnow()

            await db.commit()

            # Delete Redis record
            await redis.delete(f"email_verification:{user_id}:{code}")

            logger.info(f"User {user_id} verification code verified successfully")
            return True, "Verification successful"

        except Exception as e:
            logger.error(f"Exception during verification code validation: {e}")
            return False, "Error during verification process"

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
        """Resend verification code.

        Args:
            db: Database session.
            redis: Redis client.
            user_id: User ID.
            username: Username.
            email: Email address.
            ip_address: IP address.
            user_agent: User agent info.
            country_code: Country code (for selecting email language).

        Returns:
            (success, message, {'id': 'message_id'})
        """
        try:
            # Avoid unused parameter warning
            _ = user_agent
            # Check if email verification feature is enabled
            if not settings.enable_email_verification:
                logger.debug(f"Email verification is disabled, skipping resend for user {user_id}")
                return True, "Verification code sent (email verification disabled)", {"id": "disabled"}

            # Check resend rate limit (60 seconds between sends)
            rate_limit_key = f"email_verification_rate_limit:{user_id}"
            if await redis.get(rate_limit_key):
                return False, "Please wait 60 seconds before resending", {"id": ""}

            # Set rate limit
            await redis.setex(rate_limit_key, 60, "1")

            # Generate new verification code
            response = await EmailVerificationService.send_verification_email(
                db, redis, user_id, username, email, ip_address, user_agent, country_code
            )

            if response and response.get("id"):
                return True, "Verification code resent", response
            else:
                return False, "Resend failed, please try again later", {"id": ""}

        except Exception as e:
            logger.error(f"Exception during resending verification code: {e}")
            return False, "Error during resend process", {"id": ""}


class LoginSessionService:
    """Login session service."""

    # Session verification interface methods
    @staticmethod
    async def find_for_verification(db: AsyncSession, token: str) -> LoginSession | None:
        """Find session by session ID for verification."""
        try:
            result = await db.exec(
                select(LoginSession).where(
                    col(LoginSession.token).has(col(OAuthToken.access_token) == token),
                    LoginSession.expires_at > utcnow(),
                )
            )
            return result.first()
        except Exception:
            return None

    @staticmethod
    def get_key_for_event(session_id: str) -> str:
        """Get session key for event broadcasting."""
        return f"g0v0:{session_id}"

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
    ) -> LoginSession:
        """Create login session."""
        session = LoginSession(
            user_id=user_id,
            token_id=token_id,
            ip_address=ip_address,
            user_agent=user_agent,
            is_new_device=is_new_device,
            expires_at=utcnow() + timedelta(hours=24),  # 24-hour expiration
            is_verified=is_verified,
            web_uuid=web_uuid,
        )

        db.add(session)
        await db.commit()
        await db.refresh(session)

        logger.info(f"Created session for user {user_id} (new device: {is_new_device})")
        return session

    @classmethod
    def _session_verify_redis_key(cls, user_id: int, token_id: int) -> str:
        return f"session_verification_method:{user_id}:{token_id}"

    @classmethod
    async def get_login_method(cls, user_id: int, token_id: int, redis: Redis) -> Literal["totp", "mail"] | None:
        return await redis.get(cls._session_verify_redis_key(user_id, token_id))

    @classmethod
    async def set_login_method(cls, user_id: int, token_id: int, method: Literal["totp", "mail"], redis: Redis) -> None:
        await redis.set(cls._session_verify_redis_key(user_id, token_id), method)

    @classmethod
    async def clear_login_method(cls, user_id: int, token_id: int, redis: Redis) -> None:
        await redis.delete(cls._session_verify_redis_key(user_id, token_id))

    @staticmethod
    async def check_trusted_device(
        db: AsyncSession, user_id: int, ip_address: str, user_agent: UserAgentInfo, web_uuid: str | None = None
    ) -> bool:
        if user_agent.is_client:
            query = select(exists()).where(
                TrustedDevice.user_id == user_id,
                TrustedDevice.client_type == "client",
                TrustedDevice.ip_address == ip_address,
                TrustedDevice.expires_at > utcnow(),
            )
        else:
            if web_uuid is None:
                return False
            query = select(exists()).where(
                TrustedDevice.user_id == user_id,
                TrustedDevice.client_type == "web",
                TrustedDevice.web_uuid == web_uuid,
                TrustedDevice.expires_at > utcnow(),
            )
        return (await db.exec(query)).first() or False

    @staticmethod
    async def create_trusted_device(
        db: AsyncSession,
        user_id: int,
        ip_address: str,
        user_agent: UserAgentInfo,
        web_uuid: str | None = None,
    ) -> TrustedDevice:
        device = TrustedDevice(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent.raw_ua,
            client_type="client" if user_agent.is_client else "web",
            web_uuid=web_uuid if not user_agent.is_client else None,
            expires_at=utcnow() + timedelta(days=settings.device_trust_duration_days),
        )
        db.add(device)
        await db.commit()
        await db.refresh(device)
        return device

    @staticmethod
    async def get_or_create_trusted_device(
        db: AsyncSession,
        user_id: int,
        ip_address: str,
        user_agent: UserAgentInfo,
        web_uuid: str | None = None,
    ) -> TrustedDevice:
        if user_agent.is_client:
            query = select(TrustedDevice).where(
                TrustedDevice.user_id == user_id,
                TrustedDevice.client_type == "client",
                TrustedDevice.ip_address == ip_address,
            )
        else:
            if web_uuid is None:
                raise ValueError("web_uuid is required for web clients")
            query = select(TrustedDevice).where(
                TrustedDevice.user_id == user_id,
                TrustedDevice.client_type == "web",
                TrustedDevice.web_uuid == web_uuid,
            )

        device = (await db.exec(query)).first()
        if device is None:
            device = await LoginSessionService.create_trusted_device(db, user_id, ip_address, user_agent, web_uuid)
        else:
            device.last_used_at = utcnow()
            device.expires_at = utcnow() + timedelta(days=settings.device_trust_duration_days)
            await db.commit()
            await db.refresh(device)
        return device

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
        """Mark user's unverified sessions as verified."""
        device_info: TrustedDevice | None = None
        if user_agent.is_client or web_uuid:
            device_info = await LoginSessionService.get_or_create_trusted_device(
                db, user_id, ip_address, user_agent, web_uuid
            )

        try:
            # Find all unverified and unexpired sessions for user
            result = await db.exec(
                select(LoginSession).where(
                    LoginSession.user_id == user_id,
                    LoginSession.is_verified == False,  # noqa: E712
                    LoginSession.expires_at > utcnow(),
                    LoginSession.token_id == token_id,
                )
            )
            sessions = result.all()

            # Mark all sessions as verified
            for session in sessions:
                session.is_verified = True
                session.verified_at = utcnow()
                if device_info:
                    session.device_id = device_info.id

            if sessions:
                logger.info(f"Marked {len(sessions)} session(s) as verified for user {user_id}")

            await LoginSessionService.clear_login_method(user_id, token_id, redis)
            await db.commit()

            return len(sessions) > 0

        except Exception as e:
            logger.error(f"Exception during marking sessions as verified: {e}")
            return False

    @staticmethod
    async def check_is_need_verification(db: AsyncSession, user_id: int, token_id: int) -> bool:
        """Check if user needs verification (has unverified sessions)."""
        if settings.enable_totp_verification or settings.enable_email_verification:
            unverified_session = (
                await db.exec(
                    select(exists()).where(
                        LoginSession.user_id == user_id,
                        col(LoginSession.is_verified).is_(False),  # pyright: ignore[reportAttributeAccessIssue]
                        LoginSession.expires_at > utcnow(),
                        LoginSession.token_id == token_id,
                    )
                )
            ).first()
            return unverified_session or False
        return False
