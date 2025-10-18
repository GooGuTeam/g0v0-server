"""
密码重置服务
"""

from datetime import datetime
import json
import secrets
import string

from app.auth import get_password_hash, invalidate_user_tokens
from app.database import User
from app.dependencies.database import with_db
from app.log import logger
from app.service.email_queue import email_queue  # 导入邮件队列
from app.service.email_service import EmailService
from app.utils import utcnow

from redis.asyncio import Redis
from sqlmodel import select


class PasswordResetService:
    """密码重置服务 - 使用Redis管理验证码"""

    # Redis键前缀
    RESET_CODE_PREFIX = "password_reset:code:"  # 存储验证码
    RESET_RATE_LIMIT_PREFIX = "password_reset:rate_limit:"  # 限制请求频率

    def __init__(self):
        self.email_service = EmailService()

    def generate_reset_code(self) -> str:
        """生成8位重置验证码"""
        return "".join(secrets.choice(string.digits) for _ in range(8))

    def _get_reset_code_key(self, email: str) -> str:
        """获取验证码Redis键"""
        return f"{self.RESET_CODE_PREFIX}{email.lower()}"

    def _get_rate_limit_key(self, email: str) -> str:
        """获取频率限制Redis键"""
        return f"{self.RESET_RATE_LIMIT_PREFIX}{email.lower()}"

    async def request_password_reset(
        self, email: str, ip_address: str, user_agent: str, redis: Redis
    ) -> tuple[bool, str]:
        """
        请求密码重置

        Args:
            email: 邮箱地址
            ip_address: 请求IP
            user_agent: 用户代理
            redis: Redis连接

        Returns:
            Tuple[success, message]
        """
        email = email.lower().strip()

        async with with_db() as session:
            # 查找用户
            user_query = select(User).where(User.email == email)
            user_result = await session.exec(user_query)
            user = user_result.first()

            if not user:
                # 为了安全考虑，不告诉用户邮箱不存在，但仍然要检查频率限制
                rate_limit_key = self._get_rate_limit_key(email)
                if await redis.get(rate_limit_key):
                    return False, "请求过于频繁，请稍后再试"
                # 设置一个假的频率限制，防止恶意用户探测邮箱
                await redis.setex(rate_limit_key, 60, "1")
                return True, "如果该邮箱地址存在，您将收到密码重置邮件"

            # 检查频率限制
            rate_limit_key = self._get_rate_limit_key(email)
            if await redis.get(rate_limit_key):
                return False, "请求过于频繁，请稍后再试"

            # 生成重置验证码
            reset_code = self.generate_reset_code()

            # 存储验证码信息到Redis
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
                # 先设置频率限制
                await redis.setex(rate_limit_key, 60, "1")
                # 存储验证码，10分钟过期
                await redis.setex(reset_code_key, 600, json.dumps(reset_data))

                # 发送重置邮件
                email_sent = await self.send_password_reset_email(email=email, code=reset_code, username=user.username)

                if email_sent:
                    logger.info(f"Sent reset code to user {user.id} ({email})")
                    return True, "密码重置邮件已发送，请查收邮箱"
                else:
                    # 邮件发送失败，清理Redis中的数据
                    await redis.delete(reset_code_key)
                    await redis.delete(rate_limit_key)
                    logger.warning(f"Email sending failed, cleaned up Redis data for {email}")
                    return False, "邮件发送失败，请稍后重试"

            except Exception:
                # Redis操作失败，清理可能的部分数据
                try:
                    await redis.delete(reset_code_key)
                    await redis.delete(rate_limit_key)
                except Exception:
                    logger.warning("Failed to clean up Redis data after error")
                logger.exception("Redis operation failed")
                return False, "服务暂时不可用，请稍后重试"

    async def send_password_reset_email(self, email: str, code: str, username: str) -> bool:
        """发送密码重置邮件（使用邮件队列）"""
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

            # 纯文本内容（作为备用）
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

            # 添加邮件到队列
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
        重置密码

        Args:
            email: 邮箱地址
            reset_code: 重置验证码
            new_password: 新密码
            ip_address: 请求IP
            redis: Redis连接

        Returns:
            Tuple[success, message]
        """
        email = email.lower().strip()
        reset_code = reset_code.strip()

        async with with_db() as session:
            # 从Redis获取验证码数据
            reset_code_key = self._get_reset_code_key(email)
            reset_data_str = await redis.get(reset_code_key)

            if not reset_data_str:
                return False, "验证码无效或已过期"

            try:
                reset_data = json.loads(reset_data_str)
            except json.JSONDecodeError:
                return False, "验证码数据格式错误"

            # 验证验证码
            if reset_data.get("reset_code") != reset_code:
                return False, "验证码错误"

            # 检查是否已使用
            if reset_data.get("used", False):
                return False, "验证码已使用"

            # 验证邮箱匹配
            if reset_data.get("email") != email:
                return False, "邮箱地址不匹配"

            # 查找用户
            user_query = select(User).where(User.email == email)
            user_result = await session.exec(user_query)
            user = user_result.first()

            if not user:
                return False, "用户不存在"

            if user.id is None:
                return False, "用户ID无效"

            # 验证用户ID匹配
            if reset_data.get("user_id") != user.id:
                return False, "用户信息不匹配"

            # 密码强度检查
            if len(new_password) < 6:
                return False, "密码长度至少为6位"

            try:
                # 先标记验证码为已使用（在数据库操作之前）
                reset_data["used"] = True
                reset_data["used_at"] = utcnow().isoformat()

                # 保存用户ID用于日志记录
                user_id = user.id

                # 更新用户密码
                password_hash = get_password_hash(new_password)
                user.pw_bcrypt = password_hash  # 使用正确的字段名称 pw_bcrypt 而不是 password_hash

                # 提交数据库更改
                await session.commit()

                # 使该用户的所有现有令牌失效（使其他客户端登录失效）
                tokens_deleted = await invalidate_user_tokens(session, user_id)

                # 数据库操作成功后，更新Redis状态
                await redis.setex(reset_code_key, 300, json.dumps(reset_data))  # 保留5分钟用于日志记录

                logger.info(
                    f"User {user_id} ({email}) successfully reset password from IP {ip_address},"
                    f" invalidated {tokens_deleted} tokens"
                )
                return True, "密码重置成功，所有设备已被登出"

            except Exception as e:
                # 不要在异常处理中访问user.id，可能触发数据库操作
                user_id = reset_data.get("user_id", "未知")
                logger.error(f"Failed to reset password for user {user_id}: {e}")
                await session.rollback()

                # 数据库回滚时，需要恢复Redis中的验证码状态
                try:
                    # 恢复验证码为未使用状态
                    original_reset_data = {
                        "user_id": reset_data.get("user_id"),
                        "email": reset_data.get("email"),
                        "reset_code": reset_data.get("reset_code"),
                        "created_at": reset_data.get("created_at"),
                        "ip_address": reset_data.get("ip_address"),
                        "user_agent": reset_data.get("user_agent"),
                        "used": False,  # 恢复为未使用状态
                    }

                    # 计算剩余的TTL时间
                    created_at = datetime.fromisoformat(reset_data.get("created_at", ""))
                    elapsed = (utcnow() - created_at).total_seconds()
                    remaining_ttl = max(0, 600 - int(elapsed))  # 600秒总过期时间

                    if remaining_ttl > 0:
                        await redis.setex(
                            reset_code_key,
                            remaining_ttl,
                            json.dumps(original_reset_data),
                        )
                        logger.info(f"Restored Redis state after database rollback for {email}")
                    else:
                        # 如果已经过期，直接删除
                        await redis.delete(reset_code_key)
                        logger.info(f"Removed expired reset code after database rollback for {email}")

                except Exception as redis_error:
                    logger.error(f"Failed to restore Redis state after rollback: {redis_error}")

                return False, "密码重置失败，请稍后重试"

    async def get_reset_attempts_count(self, email: str, redis: Redis) -> int:
        """
        获取邮箱的重置尝试次数（通过检查频率限制键）

        Args:
            email: 邮箱地址
            redis: Redis连接

        Returns:
            尝试次数
        """
        try:
            rate_limit_key = self._get_rate_limit_key(email)
            ttl = await redis.ttl(rate_limit_key)
            return 1 if ttl > 0 else 0
        except Exception as e:
            logger.error(f"Failed to get attempts count: {e}")
            return 0


# 全局密码重置服务实例
password_reset_service = PasswordResetService()
