"""Email delivery and queueing services."""

from __future__ import annotations

from .email_queue import email_queue, start_email_processor, stop_email_processor
from .email_service import EmailService

__all__ = [
    "EmailService",
    "email_queue",
    "start_email_processor",
    "stop_email_processor",
]
