"""
邮件验证管理服务
"""

from datetime import timedelta
import secrets
import string
from typing import Literal

from app.config import settings
from app.database.auth import OAuthToken
from app.database.verification import EmailVerification, LoginSession, TrustedDevice
from app.log import logger
from app.models.model import UserAgentInfo
from app.service.email_queue import email_queue
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
    async def send_verification_email_via_queue(
        email: str, code: str, username: str, user_id: int, country_code: str | None = None
    ) -> dict[str, str]:
        """使用邮件队列发送验证邮件

        Args:
            email: 接收验证码的邮箱地址
            code: 验证码
            username: 用户名
            user_id: 用户ID
            country_code: 国家代码（用于选择邮件语言）

        Returns:
            返回格式为 {'id': 'message_id'} 的字典，如果使用 SMTP 则返回 email_id
        """
        try:
            from app.service.email_template_service import get_email_template_service

            # 使用模板服务生成邮件内容
            template_service = get_email_template_service()
            subject, html_content, plain_content = template_service.render_verification_email(
                username=username,
                code=code,
                country_code=country_code,
                expiry_minutes=10,
            )
            # 准备元数据
            metadata = {"type": "email_verification", "user_id": user_id, "code": code, "country": country_code}

            # 如果使用 MailerSend，直接发送并返回 message_id
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
                # 使用 SMTP 队列发送
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
                EmailVerification.is_used == False,  # noqa: E712
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
        """发送验证邮件

        Args:
            db: 数据库会话
            redis: Redis 客户端
            user_id: 用户ID
            username: 用户名
            email: 邮箱地址
            ip_address: IP 地址
            user_agent: 用户代理信息
            country_code: 国家代码（用于选择邮件语言）

        Returns:
            返回格式为 {'id': 'message_id'} 的字典
        """
        try:
            # 检查是否启用邮件验证功能
            if not settings.enable_email_verification:
                logger.debug(f"Email verification is disabled, skipping for user {user_id}")
                return {"id": "disabled"}  # 返回特殊ID表示功能已禁用

            # 检测客户端信息
            logger.info(f"Detected client for user {user_id}: {user_agent}")

            # 创建验证记录
            (
                _,
                code,
            ) = await EmailVerificationService.create_verification_record(
                db, redis, user_id, email, ip_address, user_agent.raw_ua if user_agent else None
            )

            # 使用邮件队列发送验证邮件
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
        """验证邮箱验证码"""
        try:
            # 检查是否启用邮件验证功能
            if not settings.enable_email_verification:
                logger.debug(f"Email verification is disabled, auto-approving for user {user_id}")
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
                    EmailVerification.is_used == False,  # noqa: E712
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

            logger.info(f"User {user_id} verification code verified successfully")
            return True, "验证成功"

        except Exception as e:
            logger.error(f"Exception during verification code validation: {e}")
            return False, "验证过程中发生错误"

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
        """重新发送验证码

        Args:
            db: 数据库会话
            redis: Redis 客户端
            user_id: 用户ID
            username: 用户名
            email: 邮箱地址
            ip_address: IP 地址
            user_agent: 用户代理信息
            country_code: 国家代码（用于选择邮件语言）

        Returns:
            (是否成功, 消息, {'id': 'message_id'})
        """
        try:
            # 避免未使用参数警告
            _ = user_agent
            # 检查是否启用邮件验证功能
            if not settings.enable_email_verification:
                logger.debug(f"Email verification is disabled, skipping resend for user {user_id}")
                return True, "验证码已发送（邮件验证功能已禁用）", {"id": "disabled"}

            # 检查重发频率限制（60秒内只能发送一次）
            rate_limit_key = f"email_verification_rate_limit:{user_id}"
            if await redis.get(rate_limit_key):
                return False, "请等待60秒后再重新发送", {"id": ""}

            # 设置频率限制
            await redis.setex(rate_limit_key, 60, "1")

            # 生成新的验证码
            response = await EmailVerificationService.send_verification_email(
                db, redis, user_id, username, email, ip_address, user_agent, country_code
            )

            if response and response.get("id"):
                return True, "验证码已重新发送", response
            else:
                return False, "重新发送失败，请稍后再试", {"id": ""}

        except Exception as e:
            logger.error(f"Exception during resending verification code: {e}")
            return False, "重新发送过程中发生错误", {"id": ""}


class LoginSessionService:
    """登录会话服务"""

    # Session verification interface methods
    @staticmethod
    async def find_for_verification(db: AsyncSession, token: str) -> LoginSession | None:
        """根据会话ID查找会话用于验证"""
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
        """获取用于事件广播的会话密钥"""
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
        """创建登录会话"""
        session = LoginSession(
            user_id=user_id,
            token_id=token_id,
            ip_address=ip_address,
            user_agent=user_agent,
            is_new_device=is_new_device,
            expires_at=utcnow() + timedelta(hours=24),  # 24小时过期
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
        """标记用户的未验证会话为已验证"""
        device_info: TrustedDevice | None = None
        if user_agent.is_client or web_uuid:
            device_info = await LoginSessionService.get_or_create_trusted_device(
                db, user_id, ip_address, user_agent, web_uuid
            )

        try:
            # 查找用户所有未验证且未过期的会话
            result = await db.exec(
                select(LoginSession).where(
                    LoginSession.user_id == user_id,
                    LoginSession.is_verified == False,  # noqa: E712
                    LoginSession.expires_at > utcnow(),
                    LoginSession.token_id == token_id,
                )
            )
            sessions = result.all()

            # 标记所有会话为已验证
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
        """检查用户是否需要验证（有未验证的会话）"""
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
