"""
MailerSend 邮件发送服务
使用 MailerSend API 发送邮件
"""

from typing import Any

from app.config import settings
from app.log import logger

from mailersend import EmailBuilder, MailerSendClient


class MailerSendService:
    """MailerSend 邮件发送服务"""

    def __init__(self):
        if not settings.mailersend_api_key:
            raise ValueError("MailerSend API Key is required when email_provider is 'mailersend'")
        if not settings.mailersend_from_email:
            raise ValueError("MailerSend from email is required when email_provider is 'mailersend'")

        self.client = MailerSendClient(api_key=settings.mailersend_api_key)
        self.from_email = settings.mailersend_from_email
        self.from_name = settings.from_name

    async def send_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        html_content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """
        使用 MailerSend 发送邮件

        Args:
            to_email: 收件人邮箱地址
            subject: 邮件主题
            content: 邮件纯文本内容
            html_content: 邮件HTML内容（如果有）
            metadata: 额外元数据（未使用）

        Returns:
            返回格式为 {'id': 'message_id'} 的字典
        """
        try:
            _ = metadata  # 避免未使用参数警告

            # 构建邮件
            email_builder = EmailBuilder()
            email_builder.from_email(self.from_email, self.from_name)
            email_builder.to_many([{"email": to_email}])
            email_builder.subject(subject)

            # 优先使用 HTML 内容，否则使用纯文本
            if html_content:
                email_builder.html(html_content)
            else:
                email_builder.text(content)

            email = email_builder.build()

            # 发送邮件
            response = self.client.emails.send(email)

            # 从 APIResponse 中提取 message_id
            message_id = getattr(response, "id", "") if response else ""
            logger.info(f"Successfully sent email via MailerSend to {to_email}, message_id: {message_id}")
            return {"id": message_id}

        except Exception as e:
            logger.error(f"Failed to send email via MailerSend: {e}")
            return {"id": ""}


# 全局 MailerSend 服务实例
_mailersend_service: MailerSendService | None = None


def get_mailersend_service() -> MailerSendService:
    """获取或创建 MailerSend 服务实例"""
    global _mailersend_service
    if _mailersend_service is None:
        _mailersend_service = MailerSendService()
    return _mailersend_service
