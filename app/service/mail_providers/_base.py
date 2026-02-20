"""Base class for mail service providers.

This module defines the abstract base class for mail service providers,
which are responsible for sending emails through different services
(SMTP, MailerSend, etc.).
"""

import abc
from typing import Any


class MailServiceProvider(abc.ABC):
    """Abstract base class for mail service providers."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the mail service provider.

        Args:
            **kwargs: Provider-specific configuration options.
        """
        pass

    async def init(self) -> None:
        """Optional async initialization hook.

        Override this method to perform async initialization tasks.
        """
        pass

    @abc.abstractmethod
    async def send_email(
        self,
        to_email: str,
        subject: str,
        content: str,
        html_content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Send an email.

        Args:
            to_email: Recipient email address.
            subject: Email subject.
            content: Plain text email content.
            html_content: HTML email content (optional).
            metadata: Additional metadata (optional).

        Returns:
            Dictionary containing at least 'id' key with the message ID,
            or empty string if sending failed.
        """
        raise NotImplementedError
