"""GeoLite2 Helper Class (asynchronous).

This module provides functionality for downloading, updating, and querying
MaxMind GeoLite2 databases for IP geolocation and ASN lookups.

Classes:
    GeoIPHelper: Manages GeoLite2 database downloads and IP lookups.
    GeoIPLookupResult: TypedDict for lookup results.
"""

import asyncio
from contextlib import suppress
import hashlib
from pathlib import Path
import shutil
import time
from typing import Any, Required, TypedDict

from app.log import logger

import aiofiles
import httpx
import maxminddb


class GeoIPLookupResult(TypedDict, total=False):
    """TypedDict for GeoIP lookup results.

    Attributes:
        ip: The queried IP address.
        country_iso: ISO country code (e.g., 'US', 'JP').
        country_name: Full country name in English.
        city_name: City name in English.
        latitude: Latitude coordinate as string.
        longitude: Longitude coordinate as string.
        time_zone: Timezone identifier (e.g., 'America/New_York').
        postal_code: Postal/ZIP code.
        asn: Autonomous System Number.
        organization: ASN organization name.
    """

    ip: Required[str]
    country_iso: str
    country_name: str
    city_name: str
    latitude: str
    longitude: str
    time_zone: str
    postal_code: str
    asn: int | None
    organization: str


_BASE = "https://raw.githubusercontent.com/Loyalsoldier/geoip/release"
EDITION_URLS = {
    "Country": f"{_BASE}/GeoLite2-Country.mmdb",
    "ASN": f"{_BASE}/GeoLite2-ASN.mmdb",
}
EDITION_SHA256_URLS = {
    "Country": f"{_BASE}/GeoLite2-Country.mmdb.sha256sum",
    "ASN": f"{_BASE}/GeoLite2-ASN.mmdb.sha256sum",
}
EDITION_FILENAMES = {
    "Country": "GeoLite2-Country.mmdb",
    "ASN": "GeoLite2-ASN.mmdb",
}


class GeoIPHelper:
    """Helper class for managing and querying GeoLite2 databases.

    Provides functionality to download, update, and query MaxMind GeoLite2
    databases for IP geolocation (City) and ASN information.

    Attributes:
        dest_dir: Directory to store the database files.
        license_key: MaxMind license key for downloads.
        editions: List of database editions to manage ('City', 'ASN', etc.).
        max_age_days: Maximum age of database before re-download.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        dest_dir: str | Path = Path("./geoip"),
        editions: list[str] | None = None,
        max_age_days: int = 8,
        timeout: float = 60.0,
    ):
        """Initialize the GeoIP helper.

        Args:
            dest_dir: Directory to store database files. Defaults to './geoip'.
            editions: List of editions to manage. Defaults to ['Country', 'ASN'].
            max_age_days: Re-download if database is older. Defaults to 8.
            timeout: HTTP request timeout in seconds. Defaults to 60.0.
        """
        self.dest_dir = Path(dest_dir).expanduser()
        self.editions = list(editions or ["Country", "ASN"])
        self.max_age_days = max_age_days
        self.timeout = timeout
        self._readers: dict[str, maxminddb.Reader] = {}
        self._update_lock = asyncio.Lock()

    @staticmethod
    def _as_mapping(value: Any) -> dict[str, Any]:
        """Convert a value to a dict, returning empty dict if not a dict."""
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_str(value: Any, default: str = "") -> str:
        """Convert a value to string with a default fallback."""
        if isinstance(value, str):
            return value
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _as_int(value: Any) -> int | None:
        """Convert a value to int, returning None if not an int."""
        return value if isinstance(value, int) else None

    def _latest_file_sync(self, edition_id: str) -> Path | None:
        """Find the latest database file for an edition (sync version)."""
        directory = self.dest_dir
        if not directory.is_dir():
            return None
        candidates = list(directory.glob(f"{edition_id}*.mmdb"))
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    async def _latest_file(self, edition_id: str) -> Path | None:
        """Find the latest database file for an edition (async version)."""
        return await asyncio.to_thread(self._latest_file_sync, edition_id)

    async def _download_mmdb(self, edition: str) -> Path:
        """Download and verify a GeoLite2 .mmdb file from Loyalsoldier/geoip.

        Args:
            edition: The edition name (e.g., 'Country', 'ASN').

        Returns:
            Path to the downloaded .mmdb file.

        Raises:
            KeyError: If the edition is not supported.
            ValueError: If the SHA256 checksum does not match.
        """
        url = EDITION_URLS[edition]
        sha256_url = EDITION_SHA256_URLS[edition]
        filename = EDITION_FILENAMES[edition]
        await asyncio.to_thread(self.dest_dir.mkdir, parents=True, exist_ok=True)
        dst = self.dest_dir / filename
        tmp_path = dst.with_suffix(".mmdb.tmp")

        async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout) as client:
            sha256_resp = await client.get(sha256_url)
            sha256_resp.raise_for_status()
            expected_hash = sha256_resp.text.split()[0].lower()

            digest = hashlib.sha256()
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(tmp_path, "wb") as download_file:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            digest.update(chunk)
                            await download_file.write(chunk)

        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash:
            await asyncio.to_thread(tmp_path.unlink, missing_ok=True)
            raise ValueError(f"{filename} SHA256 mismatch: expected {expected_hash}, got {actual_hash}")

        await asyncio.to_thread(shutil.move, tmp_path, dst)
        return dst

    async def update(self, force: bool = False) -> None:
        """Update GeoLite2 databases, downloading if needed.

        Args:
            force: Force re-download regardless of age. Defaults to False.
        """
        async with self._update_lock:
            for edition in self.editions:
                edition_id = EDITION_FILENAMES[edition]
                path = await self._latest_file(edition_id.replace(".mmdb", ""))
                need_download = force or path is None

                if path:
                    mtime = await asyncio.to_thread(path.stat)
                    age_days = (time.time() - mtime.st_mtime) / 86400
                    if age_days >= self.max_age_days:
                        need_download = True
                        logger.info(
                            f"{edition_id} database is {age_days:.1f} days old "
                            f"(max: {self.max_age_days}), will download new version"
                        )
                    else:
                        logger.info(
                            f"{edition_id} database is {age_days:.1f} days old, still fresh (max: {self.max_age_days})"
                        )
                else:
                    logger.info(f"{edition_id} database not found, will download")

                if need_download:
                    logger.info(f"Downloading {edition_id} database...")
                    path = await self._download_mmdb(edition)
                    logger.info(f"{edition_id} database downloaded successfully")
                else:
                    logger.info(f"Using existing {edition_id} database")

                old_reader = self._readers.get(edition)
                if old_reader:
                    with suppress(Exception):
                        old_reader.close()
                if path is not None:
                    self._readers[edition] = maxminddb.open_database(str(path))

    def lookup(self, ip: str) -> GeoIPLookupResult:
        """Look up geolocation and ASN information for an IP address.

        Args:
            ip: The IP address to look up.

        Returns:
            GeoIPLookupResult containing location and ASN information.
        """
        res: GeoIPLookupResult = {"ip": ip}
        country_reader = self._readers.get("Country")
        if country_reader:
            data = country_reader.get(ip)
            if isinstance(data, dict):
                country = self._as_mapping(data.get("country"))
                res["country_iso"] = self._as_str(country.get("iso_code"))
                country_names = self._as_mapping(country.get("names"))
                res["country_name"] = self._as_str(country_names.get("en"))

        asn_reader = self._readers.get("ASN")
        if asn_reader:
            data = asn_reader.get(ip)
            if isinstance(data, dict):
                res["asn"] = self._as_int(data.get("autonomous_system_number"))
                res["organization"] = self._as_str(data.get("autonomous_system_organization"), default="")
        return res

    def close(self) -> None:
        """Close all database readers and release resources."""
        for reader in self._readers.values():
            with suppress(Exception):
                reader.close()
        self._readers = {}


if __name__ == "__main__":

    async def _demo() -> None:
        geo = GeoIPHelper(dest_dir="./geoip")
        await geo.update()
        print(geo.lookup("8.8.8.8"))
        geo.close()

    asyncio.run(_demo())
