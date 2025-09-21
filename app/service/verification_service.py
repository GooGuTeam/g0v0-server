"""
邮件验证管理服务
"""

from __future__ import annotations

from datetime import timedelta
import secrets
import string
from typing import Literal

from app.config import settings
from app.database.verification import EmailVerification, LoginSession
from app.log import logger
from app.service.email_queue import email_queue  # 导入邮件队列
from app.utils import utcnow

from redis.asyncio import Redis
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession


class EmailVerificationService:
    """邮件验证服务"""

    @staticmethod
    def generate_verification_code() -> str:
        """生成8位验证码"""
        return "".join(secrets.choice(string.digits) for _ in range(8))

    @staticmethod
    async def send_verification_email_via_queue(email: str, code: str, username: str, user_id: int) -> bool:
        """使用邮件队列发送验证邮件

        Args:
            email: 接收验证码的邮箱地址
            code: 验证码
            username: 用户名
            user_id: 用户ID

        Returns:
            是否成功将邮件加入队列
        """
        try:
            # HTML 邮件内容
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
            background: linear-gradient(135deg, #ff66aa, #ff9966);
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
            border: 2px solid #ff66aa;
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>osu! 邮箱验证</h1>
            <p>Email Verification</p>
        </div>

        <div class="content">
            <h2>你好 {username}！</h2>
            <p>请使用以下验证码验证您的账户：</p>

            <div class="code">{code}</div>

            <p>验证码将在 <strong>10 分钟内有效</strong>。</p>

            <div class="warning">
                <p><strong>重要提示：</strong></p>
                <ul>
                    <li>请不要与任何人分享此验证码</li>
                    <li>如果您没有请求验证码，请忽略此邮件</li>
                    <li>为了账户安全，请勿在其他网站使用相同的密码</li>
                </ul>
            </div>

            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">

            <h3>Hello {username}!</h3>
            <p>Please use the following verification code to verify your account:</p>

            <p>This verification code will be valid for <strong>10 minutes</strong>.</p>

            <p><strong>Important:</strong> Do not share this verification code with anyone. If you did not request this code, please ignore this email.</p>
        </div>

        <div class="footer">
            <p>© 2025 g0v0! Private Server. 此邮件由系统自动发送，请勿回复。</p>
            <p>This email was sent automatically, please do not reply.</p>
        </div>
    </div>
</body>
</html>
            """  # noqa: E501

            # 纯文本备用内容
            plain_content = f"""
你好 {username}！

请使用以下验证码验证您的账户：

{code}

验证码将在10分钟内有效。

重要提示：
- 请不要与任何人分享此验证码
- 如果您没有请求验证码，请忽略此邮件
- 为了账户安全，请勿在其他网站使用相同的密码

Hello {username}!
Please use the following verification code to verify your account.
This verification code will be valid for 10 minutes.

© 2025 g0v0! Private Server. 此邮件由系统自动发送，请勿回复。
This email was sent automatically, please do not reply.
"""

            # 将邮件加入队列
            subject = "邮箱验证 - Email Verification"
            metadata = {"type": "email_verification", "user_id": user_id, "code": code}

            await email_queue.enqueue_email(
                to_email=email,
                subject=subject,
                content=plain_content,
                html_content=html_content,
                metadata=metadata,
            )

            return True

        except Exception as e:
            logger.error(f"[Email Verification] Failed to enqueue email: {e}")
            return False

    @staticmethod
    def generate_session_token() -> str:
        """生成会话令牌"""
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
        """创建邮件验证记录"""

        # 检查是否有未过期的验证码
        existing_result = await db.exec(
            select(EmailVerification).where(
                EmailVerification.user_id == user_id,
                col(EmailVerification.is_used).is_(False),
                EmailVerification.expires_at > utcnow(),
            )
        )
        existing = existing_result.first()

        if existing:
            # 如果有未过期的验证码，直接返回
            return existing, existing.verification_code

        # 生成新的验证码
        code = EmailVerificationService.generate_verification_code()

        # 创建验证记录
        verification = EmailVerification(
            user_id=user_id,
            email=email,
            verification_code=code,
            expires_at=utcnow() + timedelta(minutes=10),  # 10分钟过期
            ip_address=ip_address,
            user_agent=user_agent,
        )

        db.add(verification)
        await db.commit()
        await db.refresh(verification)

        # 存储到 Redis（用于快速验证）
        await redis.setex(
            f"email_verification:{user_id}:{code}",
            600,  # 10分钟过期
            str(verification.id) if verification.id else "0",
        )

        logger.info(f"[Email Verification] Created verification code for user {user_id}: {code}")
        return verification, code

    @staticmethod
    async def send_verification_email(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        username: str,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """发送验证邮件"""
        try:
            # 检查是否启用邮件验证功能
            if not settings.enable_email_verification:
                logger.debug(f"[Email Verification] Email verification is disabled, skipping for user {user_id}")
                return True  # 返回成功，但不执行验证流程

            # 创建验证记录
            (
                _,
                code,
            ) = await EmailVerificationService.create_verification_record(
                db, redis, user_id, email, ip_address, user_agent
            )

            # 使用邮件队列发送验证邮件
            success = await EmailVerificationService.send_verification_email_via_queue(email, code, username, user_id)

            if success:
                logger.info(
                    f"[Email Verification] Successfully enqueued verification email to {email} (user: {username})"
                )
                return True
            else:
                logger.error(f"[Email Verification] Failed to enqueue verification email: {email} (user: {username})")
                return False

        except Exception as e:
            logger.error(f"[Email Verification] Exception during sending verification email: {e}")
            return False

    @staticmethod
    async def verify_email_code(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        code: str,
        ip_address: str | None = None,
    ) -> tuple[bool, str]:
        """验证邮箱验证码"""
        try:
            # 检查是否启用邮件验证功能
            if not settings.enable_email_verification:
                logger.debug(f"[Email Verification] Email verification is disabled, auto-approving for user {user_id}")
                return True, "验证成功（邮件验证功能已禁用）"

            # 先从 Redis 检查
            verification_id = await redis.get(f"email_verification:{user_id}:{code}")
            if not verification_id:
                return False, "验证码无效或已过期"

            # 从数据库获取验证记录
            result = await db.exec(
                select(EmailVerification).where(
                    EmailVerification.id == int(verification_id),
                    EmailVerification.user_id == user_id,
                    EmailVerification.verification_code == code,
                    col(EmailVerification.is_used).is_(False),
                    EmailVerification.expires_at > utcnow(),
                )
            )

            verification = result.first()
            if not verification:
                return False, "验证码无效或已过期"

            # 标记为已使用
            verification.is_used = True
            verification.used_at = utcnow()

            await db.commit()

            # 删除 Redis 记录
            await redis.delete(f"email_verification:{user_id}:{code}")

            logger.info(f"[Email Verification] User {user_id} verification code verified successfully")
            return True, "验证成功"

        except Exception as e:
            logger.error(f"[Email Verification] Exception during verification code validation: {e}")
            return False, "验证过程中发生错误"

    @staticmethod
    async def resend_verification_code(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        username: str,
        email: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[bool, str]:
        """重新发送验证码"""
        try:
            # 检查是否启用邮件验证功能
            if not settings.enable_email_verification:
                logger.debug(f"[Email Verification] Email verification is disabled, skipping resend for user {user_id}")
                return True, "验证码已发送（邮件验证功能已禁用）"

            # 检查重发频率限制（60秒内只能发送一次）
            rate_limit_key = f"email_verification_rate_limit:{user_id}"
            if await redis.get(rate_limit_key):
                return False, "请等待60秒后再重新发送"

            # 设置频率限制
            await redis.setex(rate_limit_key, 60, "1")

            # 生成新的验证码
            success = await EmailVerificationService.send_verification_email(
                db, redis, user_id, username, email, ip_address, user_agent
            )

            if success:
                return True, "验证码已重新发送"
            else:
                return False, "重新发送失败，请稍后再试"

        except Exception as e:
            logger.error(f"[Email Verification] Exception during resending verification code: {e}")
            return False, "重新发送过程中发生错误"


class LoginSessionService:
    """登录会话服务"""

    @staticmethod
    async def create_session(
        db: AsyncSession,
        redis: Redis,
        user_id: int,
        token_id: int,
        ip_address: str,
        user_agent: str | None = None,
        country_code: str | None = None,
        is_new_location: bool = False,
        is_verified: bool = False,
    ) -> LoginSession:
        """创建登录会话"""

        session_token = EmailVerificationService.generate_session_token()

        session = LoginSession(
            user_id=user_id,
            token_id=token_id,
            ip_address=ip_address,
            user_agent=None,
            country_code=country_code,
            is_new_location=is_new_location,
            expires_at=utcnow() + timedelta(hours=24),  # 24小时过期
            is_verified=is_verified,
        )

        db.add(session)
        await db.commit()
        await db.refresh(session)

        # 存储到 Redis
        await redis.setex(
            f"login_session:{session_token}",
            86400,  # 24小时
            user_id,
        )

        logger.info(f"[Login Session] Created session for user {user_id} (new location: {is_new_location})")
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
    async def check_new_location(
        db: AsyncSession, user_id: int, ip_address: str, country_code: str | None = None
    ) -> bool:
        """检查是否为新位置登录"""
        try:
            # 查看过去30天内是否有相同IP或相同国家的登录记录
            thirty_days_ago = utcnow() - timedelta(days=30)

            result = await db.exec(
                select(LoginSession).where(
                    LoginSession.user_id == user_id,
                    LoginSession.created_at > thirty_days_ago,
                    (LoginSession.ip_address == ip_address) | (LoginSession.country_code == country_code),
                )
            )

            existing_sessions = result.all()

            # 如果有历史记录，则不是新位置
            return len(existing_sessions) == 0

        except Exception as e:
            logger.error(f"[Login Session] Exception during new location check: {e}")
            # 出错时默认为新位置（更安全）
            return True

    @staticmethod
    async def mark_session_verified(db: AsyncSession, redis: Redis, user_id: int, token_id: int) -> bool:
        """标记用户的未验证会话为已验证"""
        try:
            # 查找用户所有未验证且未过期的会话
            result = await db.exec(
                select(LoginSession).where(
                    LoginSession.user_id == user_id,
                    col(LoginSession.is_verified).is_(False),
                    LoginSession.expires_at > utcnow(),
                    LoginSession.token_id == token_id,
                )
            )

            sessions = result.all()

            # 标记所有会话为已验证
            for session in sessions:
                session.is_verified = True
                session.verified_at = utcnow()

            if sessions:
                logger.info(f"[Login Session] Marked {len(sessions)} session(s) as verified for user {user_id}")

            await LoginSessionService.clear_login_method(user_id, token_id, redis)

            return len(sessions) > 0

        except Exception as e:
            logger.error(f"[Login Session] Exception during marking sessions as verified: {e}")
            return False

    @staticmethod
    async def check_is_need_verification(db: AsyncSession, user_id: int, token_id: int) -> bool:
        """检查用户是否需要验证（有未验证的会话）"""
        if settings.enable_totp_verification or settings.enable_email_verification:
            unverified_session = (
                await db.exec(
                    select(exists()).where(
                        LoginSession.user_id == user_id,
                        col(LoginSession.is_verified).is_(False),
                        LoginSession.expires_at > utcnow(),
                        LoginSession.token_id == token_id,
                    )
                )
            ).first()
            return unverified_session or False
        return False
