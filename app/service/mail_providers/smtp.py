"""SMTP delivery with explicit implicit-TLS and STARTTLS support."""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib
import ssl
from typing import Any, Literal

from app.config import settings
from app.helpers import run_in_threadpool
from app.log import logger

from ._base import MailServiceProvider

from pydantic import BaseModel, Field


class _LegacySMTPSettings(BaseModel):
    smtp_server: str = "localhost"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: Literal["auto", "ssl", "starttls", "plain"] = "auto"
    smtp_timeout: float = Field(default=10, gt=0)
    smtp_ca_file: str | None = None


class SMTPProvider(MailServiceProvider):
    def __init__(
        self,
        smtp_server: str | None = None,
        smtp_port: int | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        from_email: str | None = None,
        from_name: str | None = None,
        smtp_security: Literal["auto", "ssl", "starttls", "plain"] | None = None,
        smtp_timeout: float | None = None,
        smtp_ca_file: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        config = settings.model_dump()
        config.update(
            {
                key: value
                for key, value in {
                    "smtp_server": smtp_server,
                    "smtp_port": smtp_port,
                    "smtp_username": smtp_username,
                    "smtp_password": smtp_password,
                    "smtp_security": smtp_security,
                    "smtp_timeout": smtp_timeout,
                    "smtp_ca_file": smtp_ca_file,
                }.items()
                if value is not None
            }
        )
        smtp = _LegacySMTPSettings.model_validate(config)
        self.smtp_server = smtp.smtp_server
        self.smtp_port = smtp.smtp_port
        self.smtp_username = smtp.smtp_username
        self.smtp_password = smtp.smtp_password
        self.smtp_timeout = smtp.smtp_timeout
        self.security = smtp.smtp_security
        if self.security == "auto":
            self.security = (
                "ssl"
                if self.smtp_port == 465
                else ("starttls" if self.smtp_username and self.smtp_password else "plain")
            )
        if self.security == "plain" and (self.smtp_username or self.smtp_password):
            raise ValueError("SMTP authentication requires ssl or starttls")
        if bool(self.smtp_username) != bool(self.smtp_password):
            raise ValueError("Both SMTP username and password must be configured")
        self.ssl_context = ssl.create_default_context(cafile=smtp.smtp_ca_file)
        self.from_email = from_email or settings.from_email
        self.from_name = from_name or settings.from_name

    async def send_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        html_content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        _ = metadata
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{self.from_name} <{self.from_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        if content:
            msg.attach(MIMEText(content, "plain", "utf-8"))
        if html_content:
            msg.attach(MIMEText(html_content, "html", "utf-8"))

        def send() -> None:
            if self.security == "ssl":
                connection = smtplib.SMTP_SSL(
                    self.smtp_server, self.smtp_port, timeout=self.smtp_timeout, context=self.ssl_context
                )
            else:
                connection = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=self.smtp_timeout)
            with connection as server:
                server.ehlo_or_helo_if_needed()
                if self.security == "starttls":
                    server.starttls(context=self.ssl_context)
                    server.ehlo()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                refused = server.send_message(msg)
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)

        # Failures propagate to EmailService, which persists and schedules the retry.
        await run_in_threadpool(send)
        logger.info("SMTP message delivered")
        return {"id": ""}


MailServiceProvider = SMTPProvider
