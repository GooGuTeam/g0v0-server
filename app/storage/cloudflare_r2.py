"""Cloudflare R2 storage service implementation.

This module provides a storage service implementation using Cloudflare R2,
which is S3-compatible object storage.

Classes:
    CloudflareR2StorageService: Storage service using Cloudflare R2.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .aws_s3 import AWSS3StorageService


class CloudflareR2StorageService(AWSS3StorageService):
    """Storage service implementation using Cloudflare R2.

    Extends the AWS S3 storage service with Cloudflare R2-specific
    endpoint configuration.

    Attributes:
        account_id: The Cloudflare account ID.
    """

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        public_url_base: str | None = None,
    ):
        """Initialize the Cloudflare R2 storage service.

        Args:
            account_id: The Cloudflare account ID.
            access_key_id: R2 access key ID.
            secret_access_key: R2 secret access key.
            bucket_name: The R2 bucket name.
            public_url_base: Optional base URL for public access.
                Defaults to None.
        """
        super().__init__(
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            bucket_name=bucket_name,
            public_url_base=public_url_base,
            region_name="auto",
        )
        self.account_id = account_id

    @property
    def endpoint_url(self) -> str:
        """Get the R2 endpoint URL.

        Returns:
            The Cloudflare R2 endpoint URL.
        """
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    def get_file_name_by_url(self, url: str) -> str | None:
        """Extract the file path from a URL.

        Args:
            url: The URL to parse.

        Returns:
            The file path/key, or None if the URL is empty.
        """
        if not url:
            return None

        parsed = urlparse(url)
        path = parsed.path.lstrip("/")

        if self.public_url_base and url.startswith(self.public_url_base.rstrip("/")):
            return path

        if ".r2.cloudflarestorage.com" in parsed.netloc:
            return path

        return path or None
