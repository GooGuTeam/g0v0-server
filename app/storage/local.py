from __future__ import annotations

from pathlib import Path

from app.config import settings

from .base import StorageService

import aiofiles


class LocalStorageService(StorageService):
    def __init__(
        self,
        storage_path: str,
    ):
        self.storage_path = Path(storage_path).resolve()
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, file_path: str) -> Path:
        clean_path = file_path.lstrip("/")
        full_path = self.storage_path / clean_path

        try:
            full_path.resolve().relative_to(self.storage_path)
        except ValueError:
            raise ValueError(f"Invalid file path: {file_path}")

        return full_path

    async def write_file(
        self,
        file_path: str,
        content: bytes,
        content_type: str = "application/octet-stream",  # noqa: ARG002
        cache_control: str = "public, max-age=31536000",  # noqa: ARG002
    ) -> None:
        full_path = self._get_file_path(file_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            async with aiofiles.open(full_path, "wb") as f:
                await f.write(content)
        except OSError as e:
            raise RuntimeError(f"Failed to write file: {e}")

    async def read_file(self, file_path: str) -> bytes:
        full_path = self._get_file_path(file_path)

        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            async with aiofiles.open(full_path, "rb") as f:
                return await f.read()
        except OSError as e:
            raise RuntimeError(f"Failed to read file: {e}")

    async def delete_file(self, file_path: str) -> None:
        full_path = self._get_file_path(file_path)

        if not full_path.exists():
            return

        try:
            full_path.unlink()

            parent = full_path.parent
            while parent != self.storage_path and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError as e:
            raise RuntimeError(f"Failed to delete file: {e}")

    async def is_exists(self, file_path: str) -> bool:
        full_path = self._get_file_path(file_path)
        return full_path.exists() and full_path.is_file()

    async def get_file_url(self, file_path: str) -> str:
        return f"{settings.server_url}file/{file_path.lstrip('/')}"

    def get_file_name_by_url(self, url: str) -> str | None:
        if not url.startswith(str(settings.server_url)):
            return None
        return url[len(settings.server_url) + len("file/") :]
