"""MailerSend email service.

Sends emails using the MailerSend API.
"""

from typing import Any

from app.config import settings
from app.log import logger

from mailersend import EmailBuilder, MailerSendClient


class MailerSendService:
    """MailerSend email service.

    Provides email sending functionality using the MailerSend API.
    """

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
        Send an email.

        Args:
            to_email: Recipient email address.
            subject: Email subject.
            content: Plain text email content.
            html_content: Email HTML content (if any).
            metadata: Extra metadata (unused).

        Returns:
            Dictionary in format {'id': 'message_id'}.
        """
        try:
            _ = metadata  # Avoid unused parameter warning

            # Build email
            email_builder = EmailBuilder()
            email_builder.from_email(self.from_email, self.from_name)
            email_builder.to_many([{"email": to_email}])
            email_builder.subject(subject)

            # Prefer HTML content, otherwise use plain text
            if html_content:
                email_builder.html(html_content)
            else:
                email_builder.text(content)

            email = email_builder.build()

            # Send email
            response = self.client.emails.send(email)

            # Extract message_id from APIResponse
            message_id = getattr(response, "id", "") if response else ""
            logger.info(f"Successfully sent email via MailerSend to {to_email}, message_id: {message_id}")
            return {"id": message_id}

        except Exception as e:
            logger.error(f"Failed to send email via MailerSend: {e}")
            return {"id": ""}


# Global MailerSend service instance
_mailersend_service: MailerSendService | None = None


def get_mailersend_service() -> MailerSendService:
    """Get or create the MailerSend service instance.

    Returns:
        The MailerSendService singleton instance.
    """
    global _mailersend_service
    if _mailersend_service is None:
        _mailersend_service = MailerSendService()
    return _mailersend_service
