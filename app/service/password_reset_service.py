"""Password reset service.

Manages password reset requests using Redis for verification codes.
"""

from datetime import datetime
import json
import secrets
import string

from app.auth import get_password_hash, invalidate_user_tokens
from app.database import User
from app.dependencies.database import with_db
from app.helpers import utcnow
from app.log import logger
from app.service.email_queue import email_queue  # Import email queue
from app.service.email_service import EmailService

from redis.asyncio import Redis
from sqlmodel import select


class PasswordResetService:
    """Password reset service using Redis for verification code management.

    Attributes:
        RESET_CODE_PREFIX: Redis key prefix for reset codes.
        RESET_RATE_LIMIT_PREFIX: Redis key prefix for rate limiting.
    """

    # Redis key prefixes
    RESET_CODE_PREFIX = "password_reset:code:"  # Store verification code
    RESET_RATE_LIMIT_PREFIX = "password_reset:rate_limit:"  # Rate limit requests

    def __init__(self):
        self.email_service = EmailService()

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
                if await redis.get(rate_limit_key):
                    return False, "Request too frequent, please try again later"
                # Set a fake rate limit to prevent email enumeration
                await redis.setex(rate_limit_key, 60, "1")
                return True, "If this email exists, you will receive a password reset email"

            # Check rate limit
            rate_limit_key = self._get_rate_limit_key(email)
            if await redis.get(rate_limit_key):
                return False, "Request too frequent, please try again later"

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
                await redis.setex(rate_limit_key, 60, "1")
                # Store verification code, expires in 10 minutes
                await redis.setex(reset_code_key, 600, json.dumps(reset_data))

                # Send reset email
                email_sent = await self.send_password_reset_email(email=email, code=reset_code, username=user.username)

                if email_sent:
                    logger.info(f"Sent reset code to user {user.id} ({email})")
                    return True, "Password reset email sent, please check your inbox"
                else:
                    # Email send failed, clean up Redis data
                    await redis.delete(reset_code_key)
                    await redis.delete(rate_limit_key)
                    logger.warning(f"Email sending failed, cleaned up Redis data for {email}")
                    return False, "Email sending failed, please try again later"

            except Exception:
                # Redis operation failed, clean up partial data
                try:
                    await redis.delete(reset_code_key)
                    await redis.delete(rate_limit_key)
                except Exception:
                    logger.warning("Failed to clean up Redis data after error")
                logger.exception("Redis operation failed")
                return False, "Service temporarily unavailable, please try again later"

    async def send_password_reset_email(self, email: str, code: str, username: str) -> bool:
        """Send password reset email (using email queue)."""
        try:
            # HTML email content
            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        .container {{
            max-width: 600px;
            margin: 0 auto;
            font-family: Arial, sans-serif;
            line-height: 1.6;
        }}
        .header {{
            background: #ED8EA6;
            color: white;
            padding: 20px;
            text-align: center;
            border-radius: 10px 10px 0 0;
        }}
        .content {{
            background: #f9f9f9;
            padding: 30px;
            border: 1px solid #ddd;
        }}
        .code {{
            background: #fff;
            border: 2px solid #ED8EA6;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
            font-size: 24px;
            font-weight: bold;
            letter-spacing: 3px;
            margin: 20px 0;
            color: #333;
        }}
        .footer {{
            background: #333;
            color: #fff;
            padding: 15px;
            text-align: center;
            border-radius: 0 0 10px 10px;
            font-size: 12px;
        }}
        .warning {{
            background: #fff3cd;
            border: 1px solid #ffeaa7;
            border-radius: 5px;
            padding: 10px;
            margin: 15px 0;
            color: #856404;
        }}
        .danger {{
            background: #f8d7da;
            border: 1px solid #f5c6cb;
            border-radius: 5px;
            padding: 10px;
            margin: 15px 0;
            color: #721c24;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>osu! 密码重置</h1>
            <p>Password Reset Request</p>
        </div>

        <div class="content">
            <h2>你好 {username}！</h2>
            <p>我们收到了您的密码重置请求。如果这是您本人操作，请使用以下验证码重置密码：</p>

            <div class="code">{code}</div>

            <p>这个验证码将在 <strong>10 分钟后过期</strong>。</p>

            <div class="danger">
                <strong>⚠️ 安全提醒：</strong>
                <ul>
                    <li>请不要与任何人分享这个验证码</li>
                    <li>如果您没有请求密码重置，请立即忽略这封邮件</li>
                    <li>验证码只能使用一次</li>
                    <li>建议设置一个强密码以保护您的账户安全</li>
                </ul>
            </div>

            <p>如果您有任何问题，请联系我们的支持团队。</p>

            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">

            <h3>Hello {username}!</h3>
            <p>We received a request to reset your password. If this was you, please use the following verification code to reset your password:</p>

            <p>This verification code will expire in <strong>10 minutes</strong>.</p>

            <p><strong>Security Notice:</strong> Do not share this verification code with anyone. If you did not request a password reset, please ignore this email.</p>
        </div>

        <div class="footer">
            <p>© 2025 g0v0! Private Server. 此邮件由系统自动发送，请勿回复。</p>
            <p>This email was sent automatically, please do not reply.</p>
        </div>
    </div>
</body>
</html>
            """  # noqa: E501

            # Plain text content (as fallback)
            plain_content = f"""
你好 {username}！

我们收到了您的密码重置请求。如果这是您本人操作，请使用以下验证码重置密码：

{code}

这个验证码将在10分钟后过期。

安全提醒：
- 请不要与任何人分享这个验证码
- 如果您没有请求密码重置，请立即忽略这封邮件
- 验证码只能使用一次
- 建议设置一个强密码以保护您的账户安全

如果您有任何问题，请联系我们的支持团队。

© 2025 g0v0! Private Server. 此邮件由系统自动发送，请勿回复。
"""

            # Add email to queue
            subject = "密码重置 - Password Reset"
            metadata = {"type": "password_reset", "email": email, "code": code}

            await email_queue.enqueue_email(
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
            # Get verification code data from Redis
            reset_code_key = self._get_reset_code_key(email)
            reset_data_str = await redis.get(reset_code_key)

            if not reset_data_str:
                return False, "Verification code invalid or expired"

            try:
                reset_data = json.loads(reset_data_str)
            except json.JSONDecodeError:
                return False, "Verification code data format error"

            # Verify verification code
            if reset_data.get("reset_code") != reset_code:
                return False, "Verification code incorrect"

            # Check if already used
            if reset_data.get("used", False):
                return False, "Verification code already used"

            # Verify email matches
            if reset_data.get("email") != email:
                return False, "Email address mismatch"

            # Find user
            user_query = select(User).where(User.email == email)
            user_result = await session.exec(user_query)
            user = user_result.first()

            if not user:
                return False, "User not found"

            if user.id is None:
                return False, "Invalid user ID"

            # Verify user ID matches
            if reset_data.get("user_id") != user.id:
                return False, "User information mismatch"

            # Password strength check
            if len(new_password) < 6:
                return False, "Password must be at least 6 characters"

            try:
                # Mark verification code as used first (before database operation)
                reset_data["used"] = True
                reset_data["used_at"] = utcnow().isoformat()

                # Save user ID for logging
                user_id = user.id

                # Update user password
                password_hash = get_password_hash(new_password)
                user.pw_bcrypt = password_hash  # Use correct field name pw_bcrypt instead of password_hash

                # Commit database changes
                await session.commit()

                # Invalidate all existing tokens for this user (log out other clients)
                tokens_deleted = await invalidate_user_tokens(session, user_id)

                # After successful database operation, update Redis state
                await redis.setex(reset_code_key, 300, json.dumps(reset_data))  # Keep for 5 minutes for logging

                logger.info(
                    f"User {user_id} ({email}) successfully reset password from IP {ip_address},"
                    f" invalidated {tokens_deleted} tokens"
                )
                return True, "Password reset successful, all devices logged out"

            except Exception as e:
                # Don't access user.id in exception handling, may trigger database operation
                user_id = reset_data.get("user_id", "unknown")
                logger.error(f"Failed to reset password for user {user_id}: {e}")
                await session.rollback()

                # When database rolled back, restore verification code state in Redis
                try:
                    # Restore verification code to unused state
                    original_reset_data = {
                        "user_id": reset_data.get("user_id"),
                        "email": reset_data.get("email"),
                        "reset_code": reset_data.get("reset_code"),
                        "created_at": reset_data.get("created_at"),
                        "ip_address": reset_data.get("ip_address"),
                        "user_agent": reset_data.get("user_agent"),
                        "used": False,  # Restore to unused state
                    }

                    # Calculate remaining TTL time
                    created_at = datetime.fromisoformat(reset_data.get("created_at", ""))
                    elapsed = (utcnow() - created_at).total_seconds()
                    remaining_ttl = max(0, 600 - int(elapsed))  # 600 seconds total expiry

                    if remaining_ttl > 0:
                        await redis.setex(
                            reset_code_key,
                            remaining_ttl,
                            json.dumps(original_reset_data),
                        )
                        logger.info(f"Restored Redis state after database rollback for {email}")
                    else:
                        # If already expired, delete directly
                        await redis.delete(reset_code_key)
                        logger.info(f"Removed expired reset code after database rollback for {email}")

                except Exception as redis_error:
                    logger.error(f"Failed to restore Redis state after rollback: {redis_error}")

                return False, "Password reset failed, please try again later"

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
