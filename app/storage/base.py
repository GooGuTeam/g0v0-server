"""Base storage service interface.

This module defines the abstract base class for all storage service
implementations.

Classes:
    StorageService: Abstract base class defining the storage interface.
"""

from __future__ import annotations

import abc


class StorageService(abc.ABC):
    """Abstract base class for storage services.

    Defines the interface that all storage service implementations must
    implement for file operations.
    """

    @abc.abstractmethod
    async def write_file(
        self,
        file_path: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        cache_control: str = "public, max-age=31536000",
    ) -> None:
        """Write content to a file.

        Args:
            file_path: The path/key for the file.
            content: The file content as bytes.
            content_type: MIME type of the content. Defaults to
                'application/octet-stream'.
            cache_control: Cache-Control header value. Defaults to
                'public, max-age=31536000'.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def read_file(self, file_path: str) -> bytes:
        """Read content from a file.

        Args:
            file_path: The path/key of the file to read.

        Returns:
            The file content as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def delete_file(self, file_path: str) -> None:
        """Delete a file.

        Args:
            file_path: The path/key of the file to delete.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def is_exists(self, file_path: str) -> bool:
        """Check if a file exists.

        Args:
            file_path: The path/key of the file to check.

        Returns:
            True if the file exists, False otherwise.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get_file_url(self, file_path: str) -> str:
        """Get the public URL for a file.

        Args:
            file_path: The path/key of the file.

        Returns:
            The public URL for accessing the file.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_file_name_by_url(self, url: str) -> str | None:
        """Extract the file path from a URL.

        Args:
            url: The URL to parse.

        Returns:
            The file path/key, or None if the URL doesn't match.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Close the storage service and release resources."""
        pass
