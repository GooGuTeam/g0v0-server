"""SMTP mail service provider.

Sends emails using SMTP protocol.
"""

import asyncio
import concurrent.futures
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
from typing import Any

from app.config import settings
from app.log import logger

from ._base import MailServiceProvider

from pydantic import BaseModel


class _LegacySMTPSettings(BaseModel):
    smtp_server: str = "localhost"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""


class SMTPProvider(MailServiceProvider):
    """SMTP mail service provider.

    Sends emails using standard SMTP protocol with optional TLS support.
    """

    def __init__(
        self,
        smtp_server: str | None = None,
        smtp_port: int | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the SMTP provider.

        Args:
            smtp_server: SMTP server address. Defaults to settings.smtp_server.
            smtp_port: SMTP server port. Defaults to settings.smtp_port.
            smtp_username: SMTP username. Defaults to settings.smtp_username.
            smtp_password: SMTP password. Defaults to settings.smtp_password.
            from_email: Sender email address. Defaults to settings.from_email.
            from_name: Sender display name. Defaults to settings.from_name.
            **kwargs: Additional configuration options (ignored).
        """
        super().__init__(**kwargs)
        legacy_setting = _LegacySMTPSettings.model_validate(settings.model_dump())

        self.smtp_server = smtp_server or legacy_setting.smtp_server
        self.smtp_port = smtp_port or legacy_setting.smtp_port
        self.smtp_username = smtp_username or legacy_setting.smtp_username
        self.smtp_password = smtp_password or legacy_setting.smtp_password
        self.from_email = from_email or settings.from_email
        self.from_name = from_name or settings.from_name
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

    async def _run_in_executor(self, func, *args):
        """Run synchronous operation in thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    async def send_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        html_content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Send an email via SMTP.

        Args:
            to_email: Recipient email address.
            subject: Email subject.
            content: Plain text email content.
            html_content: HTML email content (optional).
            metadata: Additional metadata (unused).

        Returns:
            Dictionary with 'id' key (empty string for SMTP as it doesn't return message IDs).
        """
        try:
            _ = metadata  # SMTP doesn't use metadata

            # Create email
            msg = MIMEMultipart("alternative")
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = to_email
            msg["Subject"] = subject

            # Add plain text content
            if content:
                msg.attach(MIMEText(content, "plain", "utf-8"))

            # Add HTML content (if any)
            if html_content:
                msg.attach(MIMEText(html_content, "html", "utf-8"))

            # Send email - use thread pool to avoid blocking event loop
            def send_smtp_email():
                with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                    if self.smtp_username and self.smtp_password:
                        server.starttls()
                        server.login(self.smtp_username, self.smtp_password)
                    server.send_message(msg)

            await self._run_in_executor(send_smtp_email)

            logger.info(f"Successfully sent email via SMTP to {to_email}")
            return {"id": ""}  # SMTP doesn't return message IDs

        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")
            return {"id": ""}


# Export as MailServiceProvider for consistent module interface
MailServiceProvider = SMTPProvider
