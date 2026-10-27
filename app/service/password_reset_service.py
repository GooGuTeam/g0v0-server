"""Password reset service.

Manages password reset requests using Redis for verification codes.
"""

from datetime import timedelta
import json
import secrets
import string

from app.auth import get_password_hash
from app.database import LoginSession, OAuthToken, TrustedDevice, User
from app.dependencies.database import with_db
from app.helpers import run_in_threadpool, utcnow
from app.log import logger
from app.service.auth_security import check_verification_limit, release_email_send, reserve_email_send
from app.service.email_service import email_service
from app.service.verification_service import lock_user

from redis.asyncio import Redis
from sqlmodel import col, delete, select


class PasswordResetService:
    """Password reset service using Redis for verification code management.

    Attributes:
        RESET_CODE_PREFIX: Redis key prefix for reset codes.
        RESET_RATE_LIMIT_PREFIX: Redis key prefix for rate limiting.
    """

    # Redis key prefixes
    RESET_CODE_PREFIX = "password_reset:code:"  # Store verification code
    RESET_RATE_LIMIT_PREFIX = "password_reset:rate_limit:"  # Rate limit requests

    def generate_reset_code(self) -> str:
        """Generate an 8-digit reset verification code."""
        return "".join(secrets.choice(string.digits) for _ in range(8))

    def _get_reset_code_key(self, email: str) -> str:
        """Get Redis key for verification code."""
        return f"{self.RESET_CODE_PREFIX}{email.lower()}"

    def _get_rate_limit_key(self, email: str) -> str:
        """Get Redis key for rate limiting."""
        return f"{self.RESET_RATE_LIMIT_PREFIX}{email.lower()}"

    async def request_password_reset(
        self, email: str, ip_address: str, user_agent: str, redis: Redis
    ) -> tuple[bool, str]:
        """Request password reset.

        Args:
            email: Email address.
            ip_address: Request IP.
            user_agent: User agent.
            redis: Redis connection.

        Returns:
            Tuple[success, message]
        """
        email = email.lower().strip()

        async with with_db() as session:
            # Find user
            user_query = select(User).where(User.email == email)
            user_result = await session.exec(user_query)
            user = user_result.first()

            if not user:
                # For security, don't reveal if email exists, but still check rate limit
                rate_limit_key = self._get_rate_limit_key(email)
                if not await redis.set(rate_limit_key, "1", nx=True, ex=60):
                    return False, "Request too frequent, please try again later"
                # Set a fake rate limit to prevent email enumeration
                return True, "If this email exists, you will receive a password reset email"

            reservation = await reserve_email_send(redis, user.id)
            if reservation is None:
                return False, "Request too frequent, please try again later"
            rate_limit_key = self._get_rate_limit_key(email)

            # Generate reset verification code
            reset_code = self.generate_reset_code()

            # Store verification code info to Redis
            reset_code_key = self._get_reset_code_key(email)
            reset_data = {
                "user_id": user.id,
                "email": email,
                "reset_code": reset_code,
                "created_at": utcnow().isoformat(),
                "ip_address": ip_address,
                "user_agent": user_agent,
                "used": False,
            }

            try:
                # Set rate limit first
                await redis.set(rate_limit_key, "1", ex=60)
                # Store verification code, expires in 10 minutes
                await redis.set(reset_code_key, json.dumps(reset_data), ex=600)

                # Send reset email
                email_sent = await self.send_password_reset_email(
                    email=email, code=reset_code, username=user.username, country_code=user.country_code
                )

                if email_sent:
                    logger.info(f"Sent reset code to user {user.id} ({email})")
                    return True, "Password reset email sent, please check your inbox"
                else:
                    # Email send failed, clean up Redis data
                    await redis.delete(reset_code_key)
                    await redis.delete(rate_limit_key)
                    await release_email_send(redis, user.id, reservation)
                    logger.warning(f"Email sending failed, cleaned up Redis data for {email}")
                    return False, "Email sending failed, please try again later"

            except Exception:
                # Redis operation failed, clean up partial data
                try:
                    await redis.delete(reset_code_key)
                    await redis.delete(rate_limit_key)
                    await release_email_send(redis, user.id, reservation)
                except Exception:
                    logger.warning("Failed to clean up Redis data after error")
                logger.exception("Redis operation failed")
                return False, "Service temporarily unavailable, please try again later"

    async def send_password_reset_email(
        self, email: str, code: str, username: str, country_code: str | None = None
    ) -> bool:
        """Send password reset email (using email queue)."""
        try:
            subject, html_content, plain_content = email_service.render_password_reset_email(
                username=username,
                code=code,
                country_code=country_code,
            )
            metadata = {
                "type": "password_reset",
                "email": email,
                "expires_at": (utcnow() + timedelta(minutes=10)).isoformat(),
            }

            await email_service.enqueue_email(
                to_email=email,
                subject=subject,
                content=plain_content,
                html_content=html_content,
                metadata=metadata,
            )

            logger.info(f"Enqueued reset code email to {email}")
            return True

        except Exception as e:
            logger.error(f"Failed to enqueue email: {e}")
            return False

    async def reset_password(
        self,
        email: str,
        reset_code: str,
        new_password: str,
        ip_address: str,
        redis: Redis,
    ) -> tuple[bool, str]:
        """
        Reset user password.

        Args:
            email: Email address.
            reset_code: Password reset verification code.
            new_password: New password.
            ip_address: Request IP address.
            redis: Redis connection.

        Returns:
            Tuple[success, message]
        """
        email = email.lower().strip()
        reset_code = reset_code.strip()

        async with with_db() as session:
            user = (await session.exec(select(User).where(User.email == email))).first()
            if user is None:
                return False, "Verification code invalid or expired"
            user_id = user.id
            await lock_user(session, user_id)
            await check_verification_limit(redis, user_id)
            key = self._get_reset_code_key(email)
            original = await redis.get(key)
            if not original:
                return False, "Verification code invalid or expired"
            try:
                data = json.loads(original)
            except json.JSONDecodeError:
                return False, "Verification code data format error"
            if data.get("used") or data.get("user_id") != user_id or data.get("email") != email:
                return False, "Verification code invalid or expired"
            if data.get("reset_code") != reset_code:
                return False, "Verification code incorrect"
            if len(new_password) < 6:
                return False, "Password must be at least 6 characters"
            consumed = await redis.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0",
                1,
                key,
                original,
            )
            if not consumed:
                return False, "Verification code invalid or expired"
            try:
                user.pw_bcrypt = await run_in_threadpool(get_password_hash, new_password)
                for model in (LoginSession, TrustedDevice, OAuthToken):
                    await session.exec(delete(model).where(col(model.user_id) == user_id))
                await session.commit()
            except Exception:
                await session.rollback()
                # A failed/ambiguous COMMIT must not make an already consumed code reusable.
                logger.exception("Password reset transaction failed for user {}", user_id)
                return False, "Password reset failed, please try again later"
            logger.info("Password reset completed and sessions revoked for user {} from {}", user_id, ip_address)
            return True, "Password reset successful, all devices logged out"

    async def get_reset_attempts_count(self, email: str, redis: Redis) -> int:
        """Get reset attempts count for email (by checking rate limit key).

        Args:
            email: Email address.
            redis: Redis connection.

        Returns:
            Attempts count.
        """
        try:
            rate_limit_key = self._get_rate_limit_key(email)
            ttl = await redis.ttl(rate_limit_key)
            return 1 if ttl > 0 else 0
        except Exception as e:
            logger.error(f"Failed to get attempts count: {e}")
            return 0


# Global password reset service instance
password_reset_service = PasswordResetService()
