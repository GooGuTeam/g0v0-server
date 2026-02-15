"""Storage service implementations for file storage.

This module provides abstract and concrete implementations for file storage
services, supporting various backends including local filesystem, AWS S3,
and Cloudflare R2.

Classes:
    StorageService: Abstract base class for storage services.
    AWSS3StorageService: AWS S3 storage implementation.
    CloudflareR2StorageService: Cloudflare R2 storage implementation.
    LocalStorageService: Local filesystem storage implementation.
"""

from __future__ import annotations

from .aws_s3 import AWSS3StorageService
from .base import StorageService
from .cloudflare_r2 import CloudflareR2StorageService
from .local import LocalStorageService

__all__ = [
    "AWSS3StorageService",
    "CloudflareR2StorageService",
    "LocalStorageService",
    "StorageService",
]
