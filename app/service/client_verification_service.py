""" "Service for verifying client versions against known valid versions."""

import json

from app.config import settings
from app.log import logger
from app.models.version import VersionCheckResult, VersionList
from app.path import CONFIG_DIR

import aiofiles
from httpx import AsyncClient

HASHES_DIR = CONFIG_DIR / "client_versions.json"


class ClientVerificationService:
    """A service to verify client versions against known valid versions.

    Attributes:
        version_lists (list[VersionList]): A list of version lists fetched from remote sources.

    Methods:
        init(): Initialize the service by loading version data from disk and refreshing from remote.
        refresh(): Fetch the latest version lists from configured URLs and store them locally.
        load_from_disk(): Load version lists from the local JSON file.
        validate_client_version(client_version: str) -> VersionCheckResult: Validate a given client version against the known versions.
    """  # noqa: E501

    def __init__(self) -> None:
        self.original_version_lists: dict[str, list[VersionList]] = {}
        self.version_lists: list[VersionList] = []

    async def init(self) -> None:
        """Initialize the service by loading version data from disk and refreshing from remote."""
        await self.load_from_disk(first_load=True)
        await self.refresh()
        await self.load_from_disk()

    async def refresh(self) -> None:
        """Fetch the latest version lists from configured URLs and store them locally."""
        lists: dict[str, list[VersionList]] = self.original_version_lists
        async with AsyncClient() as client:
            for url in settings.client_version_urls:
                try:
                    resp = await client.get(url, timeout=10)
                    resp.raise_for_status()
                    data = resp.json()
                    if len(data) == 0:
                        logger.warning(f"Client version list from {url} is empty")
                        continue
                    lists[url] = data
                    logger.info(f"Fetched client version list from {url}, total {len(data)} clients")
                except TimeoutError:
                    logger.warning(f"Timeout when fetching client version list from {url}")
                except Exception as e:
                    logger.warning(f"Failed to fetch client version list from {url}: {e}")
        async with aiofiles.open(HASHES_DIR, "wb") as f:
            await f.write(json.dumps(lists).encode("utf-8"))

    async def load_from_disk(self, first_load: bool = False) -> None:
        """Load version lists from the local JSON file."""
        self.version_lists.clear()
        try:
            async with aiofiles.open(HASHES_DIR, "rb") as f:
                content = await f.read()
                self.original_version_lists = json.loads(content.decode("utf-8"))
                for version_list in self.original_version_lists.values():
                    self.version_lists.extend(version_list)
                if not first_load:
                    if len(self.version_lists) == 0:
                        logger.warning("Client version list is empty after loading from disk")
                    else:
                        version_counts = sum(len(vl["versions"]) for vl in self.version_lists)
                        logger.info(
                            "Loaded client version list from disk, "
                            f"total {len(self.version_lists)} clients, {version_counts} versions"
                        )
        except Exception as e:
            logger.exception(f"Failed to load client version list from disk: {e}")

    def validate_client_version(self, client_version: str) -> VersionCheckResult:
        """Validate a given client version against the known versions.

        Args:
            client_version (str): The client version string to validate.

        Returns:
            VersionCheckResult: The result of the validation.
        """
        if not settings.check_client_version:
            return VersionCheckResult(is_valid=True)
        for version_list in self.version_lists:
            for version_info in version_list["versions"]:
                for hash in version_info["hashes"]:
                    if hash == client_version:
                        return VersionCheckResult(
                            is_valid=True,
                            client_name=version_list["name"],
                            version=version_info["version"],
                            os=version_info["hashes"][hash],
                        )
        return VersionCheckResult(is_valid=False)


_client_verification_service: ClientVerificationService | None = None


def get_client_verification_service() -> ClientVerificationService:
    """Get the singleton instance of ClientVerificationService.

    Returns:
        ClientVerificationService: The singleton instance.
    """
    global _client_verification_service
    if _client_verification_service is None:
        _client_verification_service = ClientVerificationService()
    return _client_verification_service


async def init_client_verification_service() -> None:
    """Initialize the ClientVerificationService singleton."""
    service = get_client_verification_service()
    logger.info("Initializing ClientVerificationService...")
    await service.init()
