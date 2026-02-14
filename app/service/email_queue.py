"""Email queue service.

Asynchronous email sending via Redis queue.
"""

import asyncio
import concurrent.futures
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import smtplib
from typing import Any
import uuid

from app.config import settings
from app.log import logger
from app.utils import bg_tasks

import redis as sync_redis


class EmailQueue:
    """Redis email queue service.

    Manages asynchronous email sending through a Redis queue,
    supporting multiple email providers (SMTP and MailerSend).
    """

    def __init__(self):
        # Create dedicated synchronous Redis client for email queue (db=0)
        self.redis = sync_redis.from_url(settings.redis_url, decode_responses=True, db=0)
        self._processing = False
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._retry_limit = 3  # Retry limit

        # Email provider configuration
        self.email_provider = settings.email_provider

        # MailerSend service (lazy initialization)
        self._mailersend_service = None

    async def _run_in_executor(self, func, *args):
        """Run synchronous operation in thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    async def start_processing(self):
        """Start email processing task."""
        if not self._processing:
            self._processing = True
            bg_tasks.add_task(self._process_email_queue)
            logger.info("Email queue processing started")

    async def stop_processing(self):
        """Stop email processing."""
        self._processing = False
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
            html_content: Email HTML content (if any).
            metadata: Additional metadata (e.g., password reset ID).

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
            "status": "pending",  # pending, sending, sent, failed
            "retry_count": "0",
        }

        # Store email data in Redis
        await self._run_in_executor(lambda: self.redis.hset(f"email:{email_id}", mapping=email_data))

        # Set 24-hour expiration (prevent data accumulation)
        await self._run_in_executor(self.redis.expire, f"email:{email_id}", 86400)

        # Add to send queue
        await self._run_in_executor(self.redis.lpush, "email_queue", email_id)

        logger.info(f"Email enqueued with id: {email_id} to {to_email}")
        return email_id

    async def get_email_status(self, email_id: str) -> dict[str, Any]:
        """Get email sending status.

        Args:
            email_id: Email task ID.

        Returns:
            Email task status information.
        """
        email_data = await self._run_in_executor(self.redis.hgetall, f"email:{email_id}")

        # Decode byte data returned from Redis
        if email_data:
            return {
                k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8") if isinstance(v, bytes) else v
                for k, v in email_data.items()
            }

        return {"status": "not_found"}

    async def _process_email_queue(self):
        """Process the email queue."""
        logger.info("Starting email queue processor")

        while self._processing:
            try:
                # Get email ID from queue
                def brpop_operation():
                    return self.redis.brpop(["email_queue"], timeout=5)

                result = await self._run_in_executor(brpop_operation)

                if not result:
                    await asyncio.sleep(1)
                    continue

                # Unpack result (list name and value)
                _, email_id = result
                if isinstance(email_id, bytes):
                    email_id = email_id.decode("utf-8")

                # Get email data
                email_data = await self.get_email_status(email_id)
                if email_data.get("status") == "not_found":
                    logger.warning(f"Email data not found for id: {email_id}")
                    continue

                # Update status to sending
                await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "sending")

                # Attempt to send email
                success = await self._send_email(email_data)

                if success:
                    # Update status to sent
                    await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "sent")
                    await self._run_in_executor(
                        self.redis.hset,
                        f"email:{email_id}",
                        "sent_at",
                        datetime.now().isoformat(),
                    )
                    logger.info(f"Email {email_id} sent successfully to {email_data.get('to_email')}")
                else:
                    # Calculate retry count
                    retry_count = int(email_data.get("retry_count", "0")) + 1

                    if retry_count <= self._retry_limit:
                        # Re-enqueue for later retry
                        await self._run_in_executor(
                            self.redis.hset,
                            f"email:{email_id}",
                            "retry_count",
                            str(retry_count),
                        )
                        await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "pending")
                        await self._run_in_executor(
                            self.redis.hset,
                            f"email:{email_id}",
                            "last_retry",
                            datetime.now().isoformat(),
                        )

                        # Delayed retry (exponential backoff)
                        delay = 60 * (2 ** (retry_count - 1))  # 1 min, 2 min, 4 min...

                        # Create delayed task
                        bg_tasks.add_task(self._delayed_retry, email_id, delay)

                        logger.warning(f"Email {email_id} will be retried in {delay} seconds (attempt {retry_count})")
                    else:
                        # Exceeded retry limit, mark as failed
                        await self._run_in_executor(self.redis.hset, f"email:{email_id}", "status", "failed")
                        logger.error(f"Email {email_id} failed after {retry_count} attempts")

            except Exception as e:
                logger.error(f"Error processing email queue: {e}")
                await asyncio.sleep(5)  # Wait 5 seconds after error

    async def _delayed_retry(self, email_id: str, delay: int):
        """Delayed retry for sending email."""
        await asyncio.sleep(delay)
        await self._run_in_executor(self.redis.lpush, "email_queue", email_id)
        logger.info(f"Re-queued email {email_id} for retry after {delay} seconds")

    async def _send_email(self, email_data: dict[str, Any]) -> bool:
        """Send email via configured provider.

        Args:
            email_data: Email data.

        Returns:
            Whether the email was sent successfully.
        """
        if self.email_provider == "mailersend":
            return await self._send_email_mailersend(email_data)
        else:
            return await self._send_email_smtp(email_data)

    async def _send_email_smtp(self, email_data: dict[str, Any]) -> bool:
        """Send email via SMTP.

        Args:
            email_data: Email data.

        Returns:
            Whether the email was sent successfully.
        """
        try:
            # Get SMTP configuration
            smtp_server = settings.smtp_server
            smtp_port = settings.smtp_port
            smtp_username = settings.smtp_username
            smtp_password = settings.smtp_password
            from_email = settings.from_email
            from_name = settings.from_name

            # Create email
            msg = MIMEMultipart("alternative")
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = email_data.get("to_email", "")
            msg["Subject"] = email_data.get("subject", "")

            # Add plain text content
            content = email_data.get("content", "")
            if content:
                msg.attach(MIMEText(content, "plain", "utf-8"))

            # Add HTML content (if any)
            html_content = email_data.get("html_content", "")
            if html_content:
                msg.attach(MIMEText(html_content, "html", "utf-8"))

            # Send email - use thread pool to avoid blocking event loop
            def send_smtp_email():
                with smtplib.SMTP(smtp_server, smtp_port) as server:
                    if smtp_username and smtp_password:
                        server.starttls()
                        server.login(smtp_username, smtp_password)
                    server.send_message(msg)

            await self._run_in_executor(send_smtp_email)

            return True

        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")
            return False

    async def _send_email_mailersend(self, email_data: dict[str, Any]) -> bool:
        """Send email via MailerSend.

        Args:
            email_data: Email data.

        Returns:
            Whether the email was sent successfully.
        """
        try:
            # Lazy initialize MailerSend service
            if self._mailersend_service is None:
                from app.service.mailersend_service import get_mailersend_service

                self._mailersend_service = get_mailersend_service()

            # Extract email data
            to_email = email_data.get("to_email", "")
            subject = email_data.get("subject", "")
            content = email_data.get("content", "")
            html_content = email_data.get("html_content", "")
            metadata_str = email_data.get("metadata", "{}")
            metadata = json.loads(metadata_str) if metadata_str else {}

            # Send email
            response = await self._mailersend_service.send_email(
                to_email=to_email,
                subject=subject,
                content=content,
                html_content=html_content or None,
                metadata=metadata,
            )

            # Check if response contains id
            if response and response.get("id"):
                logger.info(f"Email sent via MailerSend, message_id: {response['id']}")
                return True
            else:
                logger.error("MailerSend response missing 'id'")
                return False

        except Exception as e:
            logger.error(f"Failed to send email via MailerSend: {e}")
            return False


# Global email queue instance
email_queue = EmailQueue()


# Called at application startup
async def start_email_processor():
    await email_queue.start_processing()


# Called at application shutdown
async def stop_email_processor():
    await email_queue.stop_processing()
