from __future__ import annotations

from .base import StorageService
from .providers.aws_s3 import AWSS3StorageService
from .providers.cloudflare_r2 import CloudflareR2StorageService
from .providers.local import LocalStorageService

__all__ = [
    "AWSS3StorageService",
    "CloudflareR2StorageService",
    "LocalStorageService",
    "StorageService",
]
