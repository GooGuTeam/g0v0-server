from __future__ import annotations

import abc


class StorageService(abc.ABC):
    @abc.abstractmethod
    async def write_file(
        self,
        file_path: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        cache_control: str = "public, max-age=31536000",
    ) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def read_file(self, file_path: str) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    async def delete_file(self, file_path: str) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def is_exists(self, file_path: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def get_file_url(self, file_path: str) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def get_file_name_by_url(self, url: str) -> str | None:
        raise NotImplementedError

    async def close(self) -> None:
        pass
