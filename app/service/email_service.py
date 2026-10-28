"""Email service.

Unified email service providing:
- Asynchronous email sending via Redis queue
- Jinja2 template rendering with multi-language support
- Integration with pluggable mail providers
"""

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
import json
import secrets
import string
from typing import Any, ClassVar, cast
import uuid

from app.config import settings
from app.dependencies.database import get_blocking_redis
from app.helpers import as_utc, utcnow
from app.log import logger
from app.path import STATIC_DIR
from app.service.email_queue import EmailQueue
from app.service.mail_providers import get_provider, init_provider

from jinja2 import Environment, FileSystemLoader, Template
from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT


class EmailService:
    """Unified email service.

    Combines email queue management, template rendering, and sending.
    Supports multiple email providers via the mail_providers module.

    Attributes:
        CHINESE_COUNTRIES: List of country codes for Chinese-speaking regions.
    """

    # Chinese country/region codes for language detection
    CHINESE_COUNTRIES: ClassVar[list[str]] = [
        "CN",  # Mainland China
        "TW",  # Taiwan
        "HK",  # Hong Kong
        "MO",  # Macau
        "SG",  # Singapore (has Chinese speakers)
    ]

    def __init__(self):
        """Initialize email service with Redis queue and Jinja2 templates."""
        # Redis queue setup
        self.redis: Redis = get_blocking_redis()
        self._processing = False
        self._retry_limit = 3
        self._worker: asyncio.Task | None = None

        # Jinja2 template setup
        template_dir = STATIC_DIR / "templates" / "email"
        self.template_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        logger.info(f"Email service initialized with template directory: {template_dir}")

    # ==================== Queue Management ====================

    async def start_processing(self):
        """Start email processing task."""
        if not self._processing:
            await init_provider()
            self._processing = True
            self._worker = asyncio.create_task(self._process_email_queue())
            logger.info("Email queue processing started")

    async def stop_processing(self):
        """Stop email processing."""
        self._processing = False
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        logger.info("Email queue processing stopped")

    async def enqueue_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        html_content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Enqueue email for sending.

        Args:
            to_email: Recipient email address.
            subject: Email subject.
            content: Email plain text content.
            html_content: Email HTML content (optional).
            metadata: Additional metadata (optional).

        Returns:
            Email task ID.
        """
        email_id = str(uuid.uuid4())

        email_data = {
            "id": email_id,
            "to_email": to_email,
            "subject": subject,
            "content": content,
            "html_content": html_content or "",
            "metadata": json.dumps(metadata) if metadata else "{}",
            "created_at": datetime.now().isoformat(),
            "status": "pending",
            "retry_count": "0",
        }

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(f"email:{email_id}", mapping=cast(Mapping[FieldT, EncodableT], email_data))
            pipe.expire(f"email:{email_id}", 86400)
            pipe.lpush("email_queue", email_id)
            await pipe.execute()

        logger.info(f"Email enqueued with id: {email_id} to {to_email}")
        return email_id

    async def get_email_status(self, email_id: str) -> dict[str, Any]:
        """Get email sending status.

        Args:
            email_id: Email task ID.

        Returns:
            Email task status information.
        """
        email_data = await self.redis.hgetall(f"email:{email_id}")

        if email_data:
            return {
                k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8") if isinstance(v, bytes) else v
                for k, v in email_data.items()
            }

        return {"status": "not_found"}

    async def _renew_claim(self, queue: EmailQueue, email_id: str, owner: str) -> None:
        while True:
            await asyncio.sleep(10)
            if not await queue.renew(email_id, owner):
                raise RuntimeError("Email delivery claim was lost")

    async def _process_claim(self, queue: EmailQueue, email_id: str, owner: str) -> None:
        email_data = await self.get_email_status(email_id)
        metadata = json.loads(email_data.get("metadata", "{}"))
        expires_at = metadata.get("expires_at")
        if expires_at and as_utc(datetime.fromisoformat(expires_at)) <= utcnow():
            await queue.finish(email_id, owner, "expired")
            return
        heartbeat = asyncio.create_task(self._renew_claim(queue, email_id, owner))
        delivery = asyncio.create_task(self._send_email(email_data))
        try:
            done, _ = await asyncio.wait({delivery, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat  # Lost leases or Redis errors cannot acknowledge delivery.
            success = await delivery
            await queue.finish(email_id, owner, "sent" if success else "retry", self._retry_limit)
        finally:
            heartbeat.cancel()
            delivery.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            with suppress(asyncio.CancelledError):
                await delivery

    async def _process_email_queue(self):
        """Recover abandoned claims and scheduled retries on every worker iteration."""
        queue = EmailQueue(self.redis)
        while self._processing:
            try:
                await queue.recover()
                owner = uuid.uuid4().hex
                email_id = await queue.claim(owner)
                if email_id is None:
                    await asyncio.sleep(0.5)
                    continue
                await self._process_claim(queue, email_id, owner)
            except Exception:
                logger.exception("Error processing email queue")
                await asyncio.sleep(5)

    async def _delayed_retry(self, email_id: str, delay: int):
        """Keep the existing helper while persisting its deadline instead of sleeping."""
        await self.redis.zadd("email_retries", {email_id: utcnow().timestamp() + delay})
        await self.redis.hset(f"email:{email_id}", "status", "retry")

    async def _send_email(self, email_data: dict[str, Any]) -> bool:
        """Send email via configured provider."""
        try:
            provider = get_provider()

            to_email = email_data.get("to_email", "")
            subject = email_data.get("subject", "")
            content = email_data.get("content", "")
            html_content = email_data.get("html_content", "")
            metadata_str = email_data.get("metadata", "{}")
            metadata = json.loads(metadata_str) if metadata_str else {}

            response = await provider.send_email(
                to_email=to_email,
                subject=subject,
                content=content,
                html_content=html_content or None,
                metadata=metadata,
            )

            if response and not response.get("error"):
                message_id = response.get("id", "")
                if message_id:
                    logger.info(f"Email sent successfully, message_id: {message_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    # ==================== Template Rendering ====================

    def get_language(self, country_code: str | None) -> str:
        """Get language based on country code.

        Args:
            country_code: ISO 3166-1 alpha-2 country code.

        Returns:
            Language code (zh or en).
        """
        if not country_code:
            return "en"

        country_code = country_code.upper()
        if country_code in self.CHINESE_COUNTRIES:
            return "zh"

        return "en"

    def render_template(
        self,
        template_name: str,
        language: str,
        context: dict[str, Any],
    ) -> str:
        """Render HTML template.

        Args:
            template_name: Template name (without language suffix and extension).
            language: Language code (zh or en).
            context: Template context data.

        Returns:
            Rendered template content.
        """
        try:
            template_file = f"{template_name}_{language}.html"
            template: Template = self.template_env.get_template(template_file)
            return template.render(**context)

        except Exception as e:
            logger.error(f"Failed to render template {template_name}_{language}: {e}")
            if language != "en":
                logger.warning(f"Falling back to English template for {template_name}")
                return self.render_template(template_name, "en", context)
            raise

    def render_text_template(
        self,
        template_name: str,
        language: str,
        context: dict[str, Any],
    ) -> str:
        """Render plain text template.

        Args:
            template_name: Template name (without language suffix and extension).
            language: Language code (zh or en).
            context: Template context data.

        Returns:
            Rendered plain text content.
        """
        try:
            template_file = f"{template_name}_{language}.txt"
            template: Template = self.template_env.get_template(template_file)
            return template.render(**context)

        except Exception as e:
            logger.error(f"Failed to render text template {template_name}_{language}: {e}")
            if language != "en":
                logger.warning(f"Falling back to English text template for {template_name}")
                return self.render_text_template(template_name, "en", context)
            raise

    def render_verification_email(
        self,
        username: str,
        code: str,
        country_code: str | None = None,
        expiry_minutes: int = 10,
    ) -> tuple[str, str, str]:
        """Render verification email.

        Args:
            username: Username.
            code: Verification code.
            country_code: Country code for language detection.
            expiry_minutes: Verification code expiry time in minutes.

        Returns:
            Tuple of (subject, HTML content, plain text content).
        """
        language = self.get_language(country_code)

        context = {
            "username": username,
            "code": code,
            "expiry_minutes": expiry_minutes,
            "server_name": settings.from_name,
            "year": datetime.now().year,
        }

        html_content = self.render_template("verification", language, context)
        text_content = self.render_text_template("verification", language, context)

        if language == "zh":
            subject = f"邮箱验证 - {settings.from_name}"
        else:
            subject = f"Email Verification - {settings.from_name}"

        return subject, html_content, text_content

    def render_password_reset_email(
        self,
        username: str,
        code: str,
        country_code: str | None = None,
        expiry_minutes: int = 10,
    ) -> tuple[str, str, str]:
        """Render password reset email.

        Args:
            username: Username.
            code: Reset verification code.
            country_code: Country code for language detection.
            expiry_minutes: Verification code expiry time in minutes.

        Returns:
            Tuple of (subject, HTML content, plain text content).
        """
        language = self.get_language(country_code)

        context = {
            "username": username,
            "code": code,
            "expiry_minutes": expiry_minutes,
            "server_name": settings.from_name,
            "year": datetime.now().year,
        }

        html_content = self.render_template("password_reset", language, context)
        text_content = self.render_text_template("password_reset", language, context)

        subject = f"密码重置 - {settings.from_name}" if language == "zh" else f"Password Reset - {settings.from_name}"

        return subject, html_content, text_content

    # ==================== Utility Methods ====================

    @staticmethod
    def generate_verification_code() -> str:
        """Generate an 8-digit verification code.

        Returns:
            8-digit numeric verification code.
        """
        return "".join(secrets.choice(string.digits) for _ in range(8))


# Global email service instance
email_service = EmailService()


async def start_email_processor():
    """Start email processing (called at application startup)."""
    await email_service.start_processing()


async def stop_email_processor():
    """Stop email processing (called at application shutdown)."""
    await email_service.stop_processing()
