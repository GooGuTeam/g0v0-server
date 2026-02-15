"""GeoLite2 Helper Class (asynchronous).

This module provides functionality for downloading, updating, and querying
MaxMind GeoLite2 databases for IP geolocation and ASN lookups.

Classes:
    GeoIPHelper: Manages GeoLite2 database downloads and IP lookups.
    GeoIPLookupResult: TypedDict for lookup results.
"""

import asyncio
from contextlib import suppress
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
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


BASE_URL = "https://download.maxmind.com/app/geoip_download"
EDITIONS = {
    "City": "GeoLite2-City",
    "Country": "GeoLite2-Country",
    "ASN": "GeoLite2-ASN",
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
        license_key: str | None = None,
        editions: list[str] | None = None,
        max_age_days: int = 8,
        timeout: float = 60.0,
    ):
        """Initialize the GeoIP helper.

        Args:
            dest_dir: Directory to store database files. Defaults to './geoip'.
            license_key: MaxMind license key. Can also be set via
                MAXMIND_LICENSE_KEY environment variable.
            editions: List of editions to manage. Defaults to ['City', 'ASN'].
            max_age_days: Re-download if database is older. Defaults to 8.
            timeout: HTTP request timeout in seconds. Defaults to 60.0.
        """
        self.dest_dir = Path(dest_dir).expanduser()
        self.license_key = license_key or os.getenv("MAXMIND_LICENSE_KEY")
        self.editions = list(editions or ["City", "ASN"])
        self.max_age_days = max_age_days
        self.timeout = timeout
        self._readers: dict[str, maxminddb.Reader] = {}
        self._update_lock = asyncio.Lock()

    @staticmethod
    def _safe_extract(tar: tarfile.TarFile, path: Path) -> None:
        """Safely extract a tarball to prevent path traversal attacks.

        Args:
            tar: The tarfile object to extract.
            path: The destination path.

        Raises:
            RuntimeError: If an unsafe path is detected in the archive.
        """
        base = path.resolve()
        for member in tar.getmembers():
            target = (base / member.name).resolve()
            if not target.is_relative_to(base):  # py312
                raise RuntimeError("Unsafe path in tar file")
        tar.extractall(path=base, filter="data")

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

    @staticmethod
    def _extract_tarball(src: Path, dest: Path) -> None:
        """Extract a gzipped tarball to a destination."""
        with tarfile.open(src, "r:gz") as tar:
            GeoIPHelper._safe_extract(tar, dest)

    @staticmethod
    def _find_mmdb(root: Path) -> Path | None:
        """Find the first .mmdb file in a directory tree."""
        for candidate in root.rglob("*.mmdb"):
            return candidate
        return None

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

    async def _download_and_extract(self, edition_id: str) -> Path:
        """Download and extract a GeoLite2 database.

        Args:
            edition_id: The edition ID to download (e.g., 'GeoLite2-City').

        Returns:
            Path to the extracted .mmdb file.

        Raises:
            ValueError: If the license key is not configured.
            RuntimeError: If the .mmdb file is not found in the archive.
        """
        if not self.license_key:
            raise ValueError("MaxMind License Key is missing. Please configure it via env MAXMIND_LICENSE_KEY.")

        url = f"{BASE_URL}?edition_id={edition_id}&license_key={self.license_key}&suffix=tar.gz"
        tmp_dir = Path(await asyncio.to_thread(tempfile.mkdtemp))

        try:
            tgz_path = tmp_dir / "db.tgz"
            async with (
                httpx.AsyncClient(follow_redirects=True, timeout=self.timeout) as client,
                client.stream("GET", url) as resp,
            ):
                resp.raise_for_status()
                async with aiofiles.open(tgz_path, "wb") as download_file:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            await download_file.write(chunk)

            await asyncio.to_thread(self._extract_tarball, tgz_path, tmp_dir)
            mmdb_path = await asyncio.to_thread(self._find_mmdb, tmp_dir)
            if mmdb_path is None:
                raise RuntimeError(".mmdb file not found in the archive")

            await asyncio.to_thread(self.dest_dir.mkdir, parents=True, exist_ok=True)
            dst = self.dest_dir / mmdb_path.name
            await asyncio.to_thread(shutil.move, mmdb_path, dst)
            return dst
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp_dir, ignore_errors=True)

    async def update(self, force: bool = False) -> None:
        """Update GeoLite2 databases, downloading if needed.

        Args:
            force: Force re-download regardless of age. Defaults to False.
        """
        async with self._update_lock:
            for edition in self.editions:
                edition_id = EDITIONS[edition]
                path = await self._latest_file(edition_id)
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
                    path = await self._download_and_extract(edition_id)
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
        city_reader = self._readers.get("City")
        if city_reader:
            data = city_reader.get(ip)
            if isinstance(data, dict):
                country = self._as_mapping(data.get("country"))
                res["country_iso"] = self._as_str(country.get("iso_code"))
                country_names = self._as_mapping(country.get("names"))
                res["country_name"] = self._as_str(country_names.get("en"))

                city = self._as_mapping(data.get("city"))
                city_names = self._as_mapping(city.get("names"))
                res["city_name"] = self._as_str(city_names.get("en"))

                location = self._as_mapping(data.get("location"))
                latitude = location.get("latitude")
                longitude = location.get("longitude")
                res["latitude"] = str(latitude) if latitude is not None else ""
                res["longitude"] = str(longitude) if longitude is not None else ""
                res["time_zone"] = self._as_str(location.get("time_zone"))

                postal = self._as_mapping(data.get("postal"))
                postal_code = postal.get("code")
                if postal_code is not None:
                    res["postal_code"] = self._as_str(postal_code)

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
        geo = GeoIPHelper(dest_dir="./geoip", license_key="")
        await geo.update()
        print(geo.lookup("8.8.8.8"))
        geo.close()

    asyncio.run(_demo())
